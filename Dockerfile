# Set build arguments
ARG CUDA_VERSION="12.1"
ARG TORCH_VERSION="2.3.1"
ARG CUDNN_VERSION="8"

# Base image
FROM pytorch/pytorch:${TORCH_VERSION}-cuda${CUDA_VERSION}-cudnn${CUDNN_VERSION}-runtime

# Environment
ENV MKL_THREADING_LAYER=GNU
ENV HUGGINGFACE_HUB_CACHE=/tmp/huggingface

# System deps
RUN apt-get update && \
    apt-get install -y git ca-certificates && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /tmp/huggingface

# Create and switch to the application dir
WORKDIR /src/ncdlmuse

# Now copy the rest of the source
COPY . /src/ncdlmuse/

# Upgrade pip & install the package (build-time deps will be pulled in)
# use -e to update ncdlmuse repo without rebuilding the image
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e .

# Clone & install DL* from GitHub (install from the local folder)
RUN rm -rf DLICV DLMUSE NiChart_DLMUSE && \
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
