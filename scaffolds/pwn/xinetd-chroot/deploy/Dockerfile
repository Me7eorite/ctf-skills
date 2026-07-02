FROM ubuntu:20.04

ARG CTF_UID=1000
ARG CTF_GID=1000

RUN sed -i "s/http:\/\/archive.ubuntu.com/http:\/\/mirrors.tuna.tsinghua.edu.cn/g" /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        make \
        libc6-dev \
        xinetd && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g "${CTF_GID}" ctf && \
    useradd -u "${CTF_UID}" -g "${CTF_GID}" -m ctf

WORKDIR /home/ctf

# Every absolute path below is copied inside the Docker build container.
# Keep these chroot filesystem setup commands out of host scripts.
RUN mkdir -p /home/ctf/lib64 /home/ctf/lib/x86_64-linux-gnu && \
    (cp -L /lib64/ld-linux-x86-64.so.2 /home/ctf/lib64/ 2>/dev/null || \
        cp /lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /home/ctf/lib64/ld-linux-x86-64.so.2) && \
    cp -a /lib/x86_64-linux-gnu/*.so* /home/ctf/lib/x86_64-linux-gnu/ 2>/dev/null || true

RUN mkdir -p /home/ctf/dev && \
    mknod /home/ctf/dev/null c 1 3 && \
    mknod /home/ctf/dev/zero c 1 5 && \
    mknod /home/ctf/dev/random c 1 8 && \
    mknod /home/ctf/dev/urandom c 1 9 && \
    chmod 666 /home/ctf/dev/*

RUN mkdir -p /home/ctf/bin && \
    cp /bin/sh /home/ctf/bin && \
    cp /bin/ls /home/ctf/bin && \
    cp /bin/cat /home/ctf/bin

COPY deploy/_files/ctf.xinetd /etc/xinetd.d/ctf
COPY deploy/_files/start.sh /root/start.sh
RUN echo "Blocked by ctf_xinetd" > /etc/banner_fail

RUN chmod +x /root/start.sh

COPY deploy/src/ /tmp/src/
RUN cd /tmp/src && \
    make clean && make && \
    cp {{BINARY_NAME}} /home/ctf/{{BINARY_NAME}} && \
    chmod 755 /home/ctf/{{BINARY_NAME}} && \
    rm -rf /tmp/src

RUN chown -R ctf:ctf /home/ctf && \
    chmod -R 777 /home/ctf

CMD ["/root/start.sh"]
EXPOSE {{SERVICE_PORT}}
