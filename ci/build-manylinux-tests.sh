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

./build-manylinux-test-one.sh bcrypt
./build-manylinux-test-one.sh cryptography
./build-manylinux-test-one.sh icon
#./build-manylinux-test-one.sh matplotlib #gtk+
./build-manylinux-test-one.sh pandas
./build-manylinux-test-one.sh pillow
./build-manylinux-test-one.sh pycountry
./build-manylinux-test-one.sh PyQt5
#./build-manylinux-test-one.sh pyside2 #c++
./build-manylinux-test-one.sh pytz
./build-manylinux-test-one.sh pyzmq
./build-manylinux-test-one.sh simple
./build-manylinux-test-one.sh sqlite
./build-manylinux-test-one.sh tz

popd
