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

# Create and switch to the application dir
WORKDIR /src/ncdlmuse

# Copy dependency files first for better caching
COPY pyproject.toml ./
COPY long_description.rst ./
COPY LICENSE.md ./

# Install build dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir 'setuptools>=80' build hatchling hatch-vcs nvidia-ml-py

# Copy the rest of the source
COPY . /src/ncdlmuse/

# Install the package in editable mode (with PyTorch CUDA index)
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cu121 -e .

# Install CBICA packages and pre-cache models
# Clone and install DLICV and DLMUSE from GitHub
RUN cd /tmp && \
    git clone https://github.com/CBICA/DLICV.git && \
    cd DLICV && \
    pip install --no-cache-dir . && \
    cd /tmp && \
    git clone https://github.com/CBICA/DLMUSE.git && \
    cd DLMUSE && \
    pip install --no-cache-dir . && \
    cd /tmp && \
    rm -rf DLICV DLMUSE

# Pre-cache models by running with dummy input
# The commands may fail with empty input, but models will be downloaded/cached
# Use || true to allow build to continue after model download succeeds
RUN mkdir -p /dummyinput /dummyoutput && \
    (DLICV -i /dummyinput -o /dummyoutput || true) && \
    (DLMUSE -i /dummyinput -o /dummyoutput || true) && \
    rm -rf /dummyinput /dummyoutput
    
# Install NiChart_DLMUSE 0.1.7
RUN pip install --no-cache-dir NiChart_DLMUSE==0.1.7

# Entrypoint
ENTRYPOINT ["/opt/conda/bin/ncdlmuse"]
CMD ["--help"]
