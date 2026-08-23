set -xeuo pipefail

dnf -y install gcc gcc-c++ make libffi-devel zip || microdnf -y install gcc gcc-c++ make libffi-devel zip

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
source "$HOME/.cargo/env"

python3.15 -m pip install --upgrade pip setuptools wheel

mkdir -p _bundle/lib/python3.15/site-packages

python3.15 -m pip install cryptography==50.0.0 \
  --platform manylinux_2_34_aarch64 \
  --python-version 315 \
  --implementation cp \
  --abi abi3 \
  --only-binary cryptography \
  --no-deps \
  --target _bundle/lib/python3.15/site-packages

python3.15 -c 'import re; open("requirements-no-crypto.txt","w").write(re.sub("^cryptography==.*$","",open("requirements.txt").read(),flags=re.MULTILINE))'

python3.15 -m pip install -r requirements-no-crypto.txt \
  --platform manylinux_2_34_aarch64 \
  --python-version 315 \
  --no-deps \
  --target _bundle/lib/python3.15/site-packages

cd _bundle/lib/python3.15/site-packages
rm -rf boto*
zip -r ../../../../_bundle.zip .
