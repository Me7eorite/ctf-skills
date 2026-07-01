FROM {{BASE_IMAGE}}

# This Dockerfile is executed by docker build inside an image-build container.
# Every absolute path below (/lib*, /bin/ls, /home/ctf, /dev/null) refers to
# that build container or the image filesystem, never to the host filesystem.

ARG CTF_UID=1000
ARG CTF_GID=1000
ARG BINARY_NAME={{BINARY_NAME}}

RUN apt-get update \
    && apt-get install -y --no-install-recommends xinetd libc6 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g "${CTF_GID}" ctf \
    && useradd -m -u "${CTF_UID}" -g "${CTF_GID}" ctf

WORKDIR /home/ctf

# Dockerfile-only chroot setup. These commands intentionally copy files from
# the build container into the image's /home/ctf chroot. Do not move them to
# validate.sh, start.sh, metadata.build_command, or any host-side command.
RUN cp -R /lib* /home/ctf \
    && cp -R /usr/lib* /home/ctf

RUN mkdir -p /home/ctf/dev \
    && mknod /home/ctf/dev/null c 1 3 \
    && mknod /home/ctf/dev/zero c 1 5 \
    && mknod /home/ctf/dev/random c 1 8 \
    && mknod /home/ctf/dev/urandom c 1 9 \
    && chmod 666 /home/ctf/dev/*

RUN mkdir -p /home/ctf/bin \
    && cp /bin/sh /home/ctf/bin \
    && cp /bin/ls /home/ctf/bin \
    && cp /bin/cat /home/ctf/bin

COPY _files/ctf.xinetd /etc/xinetd.d/ctf
COPY _files/start.sh /root/start.sh
RUN sed -i "s/{{CTF_UID}}/${CTF_UID}/g; s/{{CTF_GID}}/${CTF_GID}/g; s/{{BINARY_NAME}}/${BINARY_NAME}/g" /etc/xinetd.d/ctf \
    && echo "Blocked by ctf_xinetd" > /etc/banner_fail \
    && chmod +x /root/start.sh

COPY src/${BINARY_NAME} /home/ctf/${BINARY_NAME}
RUN chown -R root:ctf /home/ctf \
    && chmod -R 750 /home/ctf \
    && chmod 750 /home/ctf/${BINARY_NAME}

EXPOSE {{SERVICE_PORT}}
CMD ["/root/start.sh"]
