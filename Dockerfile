# Set build arguments
ARG CUDA_VERSION="12.1"
ARG TORCH_VERSION="2.3.1"
ARG CUDNN_VERSION="8"

# Base image
FROM pytorch/pytorch:${TORCH_VERSION}-cuda${CUDA_VERSION}-cudnn${CUDNN_VERSION}-runtime

# Environment
ENV MKL_THREADING_LAYER=GNU \
    HUGGINGFACE_HUB_CACHE=/tmp/huggingface \
    PIP_CONSTRAINT=/tmp/constraints.txt

# Create pip constraints and install system deps in one layer
RUN python - <<'PY'
import os
packages_to_pin = []
def pin(name):
    try:
        mod = __import__(name)
        ver = getattr(mod, '__version__', '').split('+')[0]
        if ver:
            packages_to_pin.append(f"{name}=={ver}")
    except Exception:
        pass
for pkg in ('torch', 'torchvision', 'torchaudio'):
    pin(pkg)
os.makedirs('/tmp', exist_ok=True)
with open('/tmp/constraints.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(packages_to_pin) + "\n")
print('Pinned constraints:', ', '.join(packages_to_pin))
PY && \
    apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates wget && \
    update-ca-certificates && \
    wget -O pandoc.deb https://github.com/jgm/pandoc/releases/download/3.1.13/pandoc-3.1.13-1-amd64.deb && \
    dpkg -i pandoc.deb && \
    rm pandoc.deb && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /tmp/huggingface

# Create and switch to the application dir
WORKDIR /src/ncdlmuse

# Copy dependency files first for better caching
COPY pyproject.toml ./
COPY long_description.rst ./
COPY LICENSE.md ./

# Upgrade pip & install build dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools==80.10.0 build hatchling hatch-vcs nvidia-ml-py

# Copy the rest of the source
COPY . /src/ncdlmuse/

# Install the package in editable mode (dependencies installed under Torch constraint)
RUN pip install --no-cache-dir -e .

# Clone & install CBICA packages and pre-cache models
RUN rm -rf DLICV DLMUSE NiChart_DLMUSE && \
    git clone --depth 1 https://github.com/CBICA/DLICV.git && \
    pip install --no-cache-dir ./DLICV && \
    git clone --depth 1 https://github.com/CBICA/DLMUSE.git && \
    pip install --no-cache-dir ./DLMUSE && \
    git clone --depth 1 https://github.com/CBICA/NiChart_DLMUSE.git && \
    pip install --no-cache-dir ./NiChart_DLMUSE && \
    mkdir -p /dummyinput /dummyoutput && \
    DLICV -i /dummyinput -o /dummyoutput && \
    DLMUSE -i /dummyinput -o /dummyoutput && \
    rm -rf DLICV DLMUSE NiChart_DLMUSE

# Runtime dir
WORKDIR /tmp/

# Entrypoint
ENTRYPOINT ["/opt/conda/bin/ncdlmuse"]
CMD ["--help"]
