#!/bin/bash

set -eux

cat <<'EOM' >runtest.sh
set -eux

. .flexci/before_install.sh

CUDNN_ROOT_DIR=/usr/local/cuda/lib MAKEFLAGS=-j8 python3 -m pip install -e . -vvv

for gen in `ls scripts | grep -E "^gen_.*\.py$"`
do
  python3 scripts/${gen}
done
for dir in $(python3 scripts/elichika_tests.py --dirs)
do
  python3 scripts/elichika_tests.py --generate ${dir}
done

for dir in model node syntax
do
  for op in `ls testcases/ch2o_tests/$dir | sed 's/\.[^\.]*$//'`
  do
    python3 testcases/ch2o_tests/${dir}/${op}.py out/ch2o_${dir}_${op} --quiet
  done
done

# scripts/runtests.py needs run_onnx binary, skip the test

python3 -m pytest -sv tests

EOM

. .flexci/common.sh

. .flexci/cache.sh
pull_chainer_whl

docker run --runtime=nvidia --memory-swap=-1 --rm -v=$(pwd):/chainer-compiler --workdir=/chainer-compiler \
    ${CI_IMAGE} /bin/bash /chainer-compiler/runtest.sh

push_chainer_whl
