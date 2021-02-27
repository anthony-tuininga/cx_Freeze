#!/bin/bash

if [ -z "${VIRTUAL_ENV}" ] ; then
	echo "Please use a virtual environment"
	exit 1
fi

if ! [ -z "${GITHUB_WORKSPACE}" ] ; then
	echo "This script should be used in local build only"
	exit 1
fi

# Get script directory
CI_DIR=$(dirname "${BASH_SOURCE[0]}")
pushd $CI_DIR

./build-test-one.sh bcrypt
./build-test-one.sh cryptography
./build-test-one.sh icon
./build-test-one.sh matplotlib
./build-test-one.sh pandas
./build-test-one.sh pillow
./build-test-one.sh pycountry
./build-test-one.sh PyQt5
./build-test-one.sh pyside2
./build-test-one.sh pytz
./build-test-one.sh pyzmq
if [ "${OSTYPE}" == "msys" ] ; then
    ./build-test-one.sh service
fi
./build-test-one.sh simple
./build-test-one.sh sqlite
./build-test-one.sh tz

popd
