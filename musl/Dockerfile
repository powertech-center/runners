FROM alpine:latest

# ── Enable community repository ────────────────────────────────────────────
RUN sed -i 's|^#\(.*community\)$|\1|' /etc/apk/repositories && apk update

# ── Core utilities ──────────────────────────────────────────────────────────
RUN apk add --no-cache \
    bash \
    git \
    git-lfs \
    ca-certificates \
    openssh-client

# ── Download & archive ──────────────────────────────────────────────────────
RUN apk add --no-cache \
    wget \
    curl \
    tar \
    xz \
    zstd \
    zip \
    unzip \
    p7zip

# ── Text processing & search ───────────────────────────────────────────────
RUN apk add --no-cache \
    jq \
    yq \
    grep \
    sed \
    diffutils \
    findutils \
    tree

# ── Build systems ──────────────────────────────────────────────────────────
RUN apk add --no-cache \
    make \
    cmake \
    ninja-build \
    pkgconf && \
    ln -s /usr/lib/ninja-build/bin/ninja /usr/local/bin/ninja

# ── C/C++ compilers & dev headers ──────────────────────────────────────────
RUN apk add --no-cache \
    gcc \
    g++ \
    musl-dev \
    linux-headers \
    clang \
    compiler-rt \
    openssl-dev \
    zlib-dev

# ── Languages ──────────────────────────────────────────────────────────────
RUN apk add --no-cache \
    python3 \
    py3-pip \
    perl \
    php \
    composer \
    ruby \
    nodejs \
    npm \
    go \
    rust \
    cargo \
    openjdk21-jdk

# ── .NET SDK ───────────────────────────────────────────────────────────────
RUN apk add --no-cache dotnet9-sdk

# ── PowerShell ─────────────────────────────────────────────────────────────
RUN apk add --no-cache powershell

# ── Node.js package managers ───────────────────────────────────────────────
RUN npm install -g yarn pnpm

CMD ["/bin/sh"]
