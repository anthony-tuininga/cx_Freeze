#!/bin/bash

if [ -z "${VIRTUAL_ENV}" ] && [ -z "${GITHUB_WORKSPACE}" ] ; then
	echo "Please use a virtual environment"
	exit 1
fi

if [ -z "${POLICY}" ] || [ -z "${PLATFORM}" ] || [ -z "${COMMIT_SHA}" ] ; then
	echo "Environment variables missing"
	exit 1
fi

# Get script directory
CI_DIR=$(dirname "${BASH_SOURCE[0]}")
TOP_DIR=${CI_DIR}/..

echo "Build the wheels"
pushd $TOP_DIR
docker run --rm -e PLAT=${POLICY}_${PLATFORM} \
    -e USER_ID=$(id -u) -e GROUP_ID=$(id -g) \
	-v `pwd`:/io \
	${POLICY}_${PLATFORM}:${COMMIT_SHA} \
	/io/ci/build-manylinux-wheels.sh
ls wheelhouse/
popd

