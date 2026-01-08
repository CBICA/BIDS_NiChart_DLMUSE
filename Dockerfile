# Set build arguments
ARG CUDA_VERSION="12.1"
ARG TORCH_VERSION="2.3.1"
ARG CUDNN_VERSION="8"

# Base image
FROM pytorch/pytorch:${TORCH_VERSION}-cuda${CUDA_VERSION}-cudnn${CUDNN_VERSION}-runtime

# Environment
ENV MKL_THREADING_LAYER=GNU \
    HUGGINGFACE_HUB_CACHE=/tmp/huggingface

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends git ca-certificates wget && \
    update-ca-certificates && \
    wget -O pandoc.deb https://github.com/jgm/pandoc/releases/download/3.1.13/pandoc-3.1.13-1-amd64.deb && \
    dpkg -i pandoc.deb && \
    rm pandoc.deb && \
    rm -rf /var/lib/apt/lists/*

# Install build dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir 'setuptools>=80' build hatchling hatch-vcs nvidia-ml-py

# Install CBICA packages first (before ncdlmuse to avoid dependency conflicts)
# Clone all repos, then install dependencies (DLICV, DLMUSE) before NiChart_DLMUSE
# Install without -e flag so packages are copied to site-packages (not symlinked)
RUN mkdir -p /tmp/cbica_repos && \
    cd /tmp/cbica_repos && \
    git clone https://github.com/CBICA/DLICV.git && \
    git clone https://github.com/CBICA/DLMUSE.git && \
    git clone https://github.com/CBICA/NiChart_DLMUSE.git && \
    cd DLICV && \
    pip install --no-cache-dir . && \
    cd ../DLMUSE && \
    pip install --no-cache-dir . && \
    cd ../NiChart_DLMUSE && \
    pip install --no-cache-dir . && \
    cd / && \
    rm -rf /tmp/cbica_repos

# Install ncdlmuse version 0.1.4
RUN pip install --no-cache-dir --force-reinstall --extra-index-url https://download.pytorch.org/whl/cu121 'torch==2.3.1+cu121' && \
    pip install --no-cache-dir ncdlmuse==0.1.4

# Pre-cache models by running with dummy input
# The commands may fail with empty input, but models will be downloaded/cached
# Use || true to allow build to continue after model download succeeds
RUN mkdir -p /dummyinput /dummyoutput && \
    (DLICV -i /dummyinput -o /dummyoutput || true) && \
    (DLMUSE -i /dummyinput -o /dummyoutput || true) && \
    rm -rf /dummyinput /dummyoutput

# Entrypoint
ENTRYPOINT ["/opt/conda/bin/ncdlmuse"]
CMD ["--help"]
