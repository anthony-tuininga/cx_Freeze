#!/bin/bash

if [ -z "${VIRTUAL_ENV}" ] && [ -z "${GITHUB_WORKSPACE}" ] ; then
	echo "Required: use of a virtual environment."
	exit 1
fi

if [ -z "$1" ] ; then
	echo "Usage: $0 sample"
	echo "Where:"
	echo "  sample is the name in samples directory (e.g. cryptography)"
	exit 1
fi
TEST_SAMPLE=$1

set -e -x

# Get script directory (without using /usr/bin/realpath)
pushd $(dirname "${BASH_SOURCE[0]}")
CI_DIR=$(pwd)
# This script is on ci subdirectory
cd ..
TOP_DIR=$(pwd)
popd

# Constants
PY_PLATFORM=$(python -c "import sysconfig; print(sysconfig.get_platform())")
PY_VERSION=$(python -c "import sysconfig; print(sysconfig.get_python_version())")

echo "Install dependencies for ${TEST_SAMPLE} sample"
TEST_REQUIRES=$(python ${CI_DIR}/build-test-json.py ${TEST_SAMPLE} req)
if ! [ -z "${TEST_REQUIRES}" ] ; then
    echo "Requirements installed: ${TEST_REQUIRES}"
fi
if ! python -c "import cx_Freeze; print(cx_Freeze.__version__)" 2>/dev/null
then
    if [ -d "${TOP_DIR}/wheelhouse" ] ; then
        echo "Install cx-freeze from wheelhouse"
        pip install --no-index -f "${TOP_DIR}/wheelhouse" cx-freeze --no-deps
    fi
fi

echo "Freeze ${TEST_SAMPLE} sample"
# Check if the samples is in current directory or in a cx_Freeze tree
if [ -d "${TEST_SAMPLE}" ] ; then
    pushd ${TEST_SAMPLE}
    TEST_DIR=$(pwd)
else
    TEST_DIR=${TOP_DIR}/cx_Freeze/samples/${TEST_SAMPLE}
    if ! [ -d "${TEST_DIR}" ] ; then
        echo "Sample's directory not found"
        exit 1
    fi
    pushd $TEST_DIR
fi
# Freeze the sample
python setup.py build_exe --excludes=tkinter --include-msvcr=true
popd

echo "Run ${TEST_SAMPLE} sample"
TEST_PRECMD=./
if [ "${OSTYPE}" == "msys" ] && [ -z "${GITHUB_WORKSPACE}" ] ; then
    TEST_PRECMD="mintty --hold always -e ./"
fi
BUILD_DIR="${TEST_DIR}/build/exe.${PY_PLATFORM}-${PY_VERSION}"
pushd ${BUILD_DIR}
count=0
TEST_NAME=$(python ${CI_DIR}/build-test-json.py ${TEST_SAMPLE} ${count})
until [ -z "${TEST_NAME}" ] ; do
    if [[ ${TEST_NAME} == gui:* ]] || [[ ${TEST_NAME} == svc:* ]] ; then
        TEST_NAME=${TEST_NAME:4}
        if ! [ -z "${GITHUB_WORKSPACE}" ] || [ "${OSTYPE}" != "msys" ] ; then
            ${TEST_PRECMD}${TEST_NAME} &
        else
            ${TEST_PRECMD}${TEST_NAME}
        fi
    else
        ${TEST_PRECMD}${TEST_NAME}
    fi
    if [ "${TEST_SAMPLE}" == "simple" ] ; then
        echo "test - rename the executable"
        if [ "${OSTYPE}" == "msys" ] ; then
            cp hello.exe Test_Hello.exe
        else
            cp hello Test_Hello
        fi
        ${TEST_PRECMD}Test_Hello ação ótica côncavo peña
    fi
    count=$(( $count + 1 ))
    TEST_NAME=$(python ${CI_DIR}/build-test-json.py ${TEST_SAMPLE} ${count})
done
popd
