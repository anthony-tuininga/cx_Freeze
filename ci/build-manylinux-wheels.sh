#!/bin/bash
set -e -u -x

function repair_wheel {
    wheel="$1"
    if ! auditwheel show "$wheel"; then
        echo "Skipping non-platform wheel $wheel"
    else
        auditwheel repair "$wheel" --plat "$PLAT" -w /io/wheelhouse/
    fi
}

# Compile wheels
pushd /io
rm -f wheelhouse/* >/dev/null || true
for PYBIN in /opt/python/*36m/bin; do
    #"${PYBIN}/pip" wheel . --no-deps -w /tmp/wheelhouse/ -vv
    "${PYBIN}/python" -m build --skip-dependencies --wheel --outdir /tmp/wheelhouse/ .
done

# Bundle external shared libraries into the wheels
for whl in /tmp/wheelhouse/*.whl; do
    repair_wheel "$whl"
done
chown -R $USER_ID:$GROUP_ID /io/wheelhouse/
for whl in /io/wheelhouse/*.whl; do
    unzip -Z -l "$whl"
done

# Install package
for PYBIN in /opt/python/*36m/bin/; do
    "${PYBIN}/pip" install cx_Freeze --no-index -f /io/wheelhouse
    "${PYBIN}/python" -m cx_Freeze --version
done
popd

