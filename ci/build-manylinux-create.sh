#!/bin/bash

if [ -z "${VIRTUAL_ENV}" ] && [ -z "${GITHUB_WORKSPACE}" ] ; then
	echo "Please use a virtual environment"
	exit 1
fi

if [ -z "${POLICY}" ] || [ -z "${PLATFORM}" ] || [ -z "${COMMIT_SHA}" ] ; then
	echo "Environment variables missing"
	exit 1
fi

if [ -d "../../manylinux" ] ; then
	pushd ../../manylinux
elif [ -d manylinux ] ; then
	pushd manylinux
else
	echo "Please checkout pypa/manylinux"
	exit 1
fi

echo "Create new branch manylinux-freeze"
git checkout -f master
git checkout -B manylinux-freeze master

echo "Patch the build scripts"
sed -i 's/quay.io\/pypa\/\$/\$/g' build.sh
# remove python 3.5
#sed -i '/RUN manylinux-entrypoint .*3.5/d' docker/Dockerfile
#sed -i '/COPY --from=build_cpython35/d' docker/Dockerfile
# enable shared .so and .lib
SCRIPT=docker/build_scripts/build-cpython.sh
#sed -i 's/--disable-shared --with-ensurepip=no/--enable-shared --enable-optimizations --with-ensurepip=no LDFLAGS=\"-Wl,-rpath,${PREFIX}\/lib\"/g' $SCRIPT
sed -i 's/--disable-shared/--enable-shared --enable-ipv6/g' $SCRIPT
sed -i 's/NODIST="${MANYLINUX_LDFLAGS}"/NODIST="${MANYLINUX_LDFLAGS} -Wl,-rpath,\${PREFIX}\/lib"/g' $SCRIPT
sed -i '/find.*.a.*rm -f/d' $SCRIPT
# enable importlib-metadata and others in py38+
sed -i 's/ python_version.*3.8.* / /g' docker/build_scripts/requirements.txt
# enable libsqlite3
sed -i '/rm .*libsqlite3.a/d' docker/build_scripts/build-sqlite3.sh
#sed -i '/rm .*\/usr\/local\/bin.*/d' docker/build_scripts/build-sqlite3.sh
#sed -i '/libsqlite3.*-delete/d' docker/build_scripts/build-sqlite3.sh

echo "Create docker image"
./build.sh

popd
