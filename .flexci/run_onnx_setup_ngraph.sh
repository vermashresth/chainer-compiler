#!/bin/bash

set -eux

. .flexci/run_onnx_setup.sh

python3 -m pip list -v
