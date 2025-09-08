# Set build arguments
ARG CUDA_VERSION="12.1"
ARG TORCH_VERSION="2.3.1"
ARG CUDNN_VERSION="8"

# Base image
FROM pytorch/pytorch:${TORCH_VERSION}-cuda${CUDA_VERSION}-cudnn${CUDNN_VERSION}-runtime

# Environment
ENV MKL_THREADING_LAYER=GNU
ENV HUGGINGFACE_HUB_CACHE=/tmp/huggingface

# Verify Python version compatibility
RUN python --version

# System deps
RUN apt-get update && \
    apt-get install -y git ca-certificates wget && \
    update-ca-certificates && \
    # Install newer pandoc with --citeproc support (2.11+)
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
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir build hatchling hatch-vcs

# Copy the rest of the source
COPY . /src/ncdlmuse/

# Install the package in editable mode
RUN pip install --no-cache-dir -e .

# Clone & install CBICA
RUN rm -rf DLICV DLMUSE NiChart_DLMUSE && \
    pip install --no-cache-dir "numpy<2" "scipy<2" && \
    git clone https://github.com/CBICA/DLICV.git && \
    pip install --no-cache-dir ./DLICV && \
    git clone https://github.com/CBICA/DLMUSE.git && \
    pip install --no-cache-dir ./DLMUSE && \
    git clone https://github.com/CBICA/NiChart_DLMUSE.git && \
    pip install --no-cache-dir ./NiChart_DLMUSE

# Create dummy I/O dirs and pre-cache models
RUN mkdir -p /dummyinput /dummyoutput && \
    DLICV -i /dummyinput -o /dummyoutput && \
    DLMUSE -i /dummyinput -o /dummyoutput

# Runtime dir
WORKDIR /tmp/

# Entrypoint
ENTRYPOINT ["/opt/conda/bin/ncdlmuse"]
CMD ["--help"]
