#!/bin/bash

if [ -z "${VIRTUAL_ENV}" ] && [ -z "${GITHUB_WORKSPACE}" ] ; then
	echo "Required: use of a virtual environment."
	exit 1
fi

if [ -z "${POLICY}" ] ; then
    POLICY=manylinux2010
fi
if [ -z "${PLATFORM}" ] ; then
    PLATFORM=x86_64
fi
if [ -z "${COMMIT_SHA}" ] ; then
    COMMIT_SHA=latest
fi

if [ -z "$1" ] ; then
	echo "Usage: $0 sample-name"
	echo "Where:"
	echo "  sample-name is the name in samples directory (e.g. cryptography)"
	exit 1
fi
TEST_SAMPLE=$1

set -e -u -x

# Get script directory
CI_DIR=$(dirname "${BASH_SOURCE[0]}")
TOP_DIR=${CI_DIR}/..
# Get the real path in a compatible way (do not use realpath)
pushd $TOP_DIR
TOP_DIR=$(pwd)
popd
SAMPLE_DIR=${TOP_DIR}/cx_Freeze/samples/${TEST_SAMPLE}

mkdir -p ${SAMPLE_DIR}/build
cat <<EOF >${SAMPLE_DIR}/build/build-test-${TEST_SAMPLE}.sh
#!/bin/bash
cd /io/cx_Freeze/samples/${TEST_SAMPLE}
for PYBIN in /opt/python/*36m/bin ; do
    echo "Freeze sample: ${TEST_SAMPLE}"
    echo "Python from: \${PYBIN}"
    echo "Platform: manylinux2010_${PLATFORM}"
    BUILD_ENV=build/env.36
    "\${PYBIN}/python" -m venv --system-site-packages \${BUILD_ENV}
    source \${BUILD_ENV}/bin/activate
    /io/ci/build-test-one.sh ${TEST_SAMPLE}
    deactivate || true
done
chown -R \$USER_ID:\$GROUP_ID build/exe.linux-${PLATFORM}-*
EOF
chmod +x ${SAMPLE_DIR}/build/build-test-${TEST_SAMPLE}.sh

docker run --rm -e PLAT=${POLICY}_${PLATFORM} \
	-e USER_ID=$(id -u) -e GROUP_ID=$(id -g) \
	-v ${TOP_DIR}:/io \
	${POLICY}_${PLATFORM}:${COMMIT_SHA} \
	/io/cx_Freeze/samples/${TEST_SAMPLE}/build/build-test-${TEST_SAMPLE}.sh

echo "Run sample isolated in a docker: ${TEST_SAMPLE}"
echo "Platform: ubuntu:16.04 ${PLATFORM}"
TEST_NAME="test_${TEST_SAMPLE}"
if [ -f ${SAMPLE_DIR}/build/exe.linux-${PLATFORM}-3.6/${TEST_NAME} ] ; then
    docker run --rm \
        -v ${SAMPLE_DIR}/build/exe.linux-${PLATFORM}-3.6:/frozen \
        ubuntu:16.04 /frozen/${TEST_NAME}
fi
