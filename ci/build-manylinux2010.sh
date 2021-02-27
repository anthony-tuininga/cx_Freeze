#!/bin/bash

if [ -z "${VIRTUAL_ENV}" ] ; then
	echo "Please use a virtual environment"
	exit 1
fi

if ! [ -z "${GITHUB_WORKSPACE}" ] ; then
	echo "This script should be used in local build only"
	exit 1
fi

echo "Prepare for local build"
export POLICY=manylinux2010
export PLATFORM=x86_64
export COMMIT_SHA=latest
docker buildx version
docker buildx create --name builder-manylinux --driver docker-container \
     --buildkitd-flags "--allow-insecure-entitlement security.insecure --allow-insecure-entitlement network.host" --use
docker buildx inspect --bootstrap --builder builder-manylinux

# Get script directory
CI_DIR=$(dirname "${BASH_SOURCE[0]}")

pushd $CI_DIR
./build-manylinux-create.sh
./build-manylinux-run.sh
./build-manylinux-tests.sh
popd
