# Per-build Electrium Windows (wine) image. The heavy deps (mingw-w64, wine-stable,
# nsis, build tools) live in the prebuilt sidgrip/electrum-wine-base; this layer
# only adds a build user matching the host UID, so `docker build` is near-instant
# and reproducible on any machine (the base auto-pulls from Docker Hub).
#
# Build/refresh the base:  contrib/build-wine/build-base.sh   (full recipe: Dockerfile.base)
FROM sidgrip/electrum-wine-base:25.2

# create new user to avoid using root; but with sudo access and no password for convenience.
ARG UID=1000
RUN if [ "$UID" != "0" ] ; then useradd --uid $UID --create-home --shell /bin/bash "user" ; fi
RUN usermod -append --groups sudo $(id -nu $UID || echo "user")
RUN echo "%sudo ALL=(ALL) NOPASSWD: ALL" >> /etc/sudoers
RUN HOME_DIR=$(getent passwd $UID | cut -d: -f6)
ENV WORK_DIR="${HOME_DIR}/wspace" \
    PATH="${HOME_DIR}/.local/bin:${PATH}"
WORKDIR ${WORK_DIR}
RUN chown --recursive ${UID} ${WORK_DIR}
RUN chown ${UID} /opt
USER ${UID}

RUN mkdir --parents "/opt/wine64/drive_c/electrum"
