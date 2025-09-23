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
RUN pip install --no-cache-dir setuptools build hatchling hatch-vcs nvidia-ml-py

# Copy the rest of the source
COPY . /src/ncdlmuse/

# Install the package in editable mode (with PyTorch CUDA index)
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cu121 -e .

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

# Entrypoint
ENTRYPOINT ["/opt/conda/bin/ncdlmuse"]
CMD ["--help"]
