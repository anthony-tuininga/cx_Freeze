#!/bin/bash

if [ -z "${VIRTUAL_ENV}" ] && [ -z "${GITHUB_WORKSPACE}" ] ; then
	echo "Please use a virtual environment"
	exit 1
fi

if [ -z "${POLICY}" ] || [ -z "${PLATFORM}" ] || [ -z "${COMMIT_SHA}" ]; then
	echo "Environment variables missing"
	exit 1
fi

echo "Install dependencies"
python -m pip install --upgrade pip
pip install cibuildwheel==1.9.0

echo "Build the wheel"
export CIBW_BUILD=cp3*-m*_${PLATFORM}
export CIBW_SKIP=cp35*
export CIBW_BUILD_VERBOSITY=1
export CIBW_BEFORE_BUILD='pip install "importlib-metadata>=3.1.1"'
export CIBW_MANYLINUX_X86_64_IMAGE=${POLICY}_${PLATFORM}:${COMMIT_SHA}
python -m cibuildwheel --platform linux --output-dir dist .

