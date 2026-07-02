FROM ubuntu:20.04

LABEL auther_template="CTF-Archives"

RUN set -eux; \
    cp /etc/apt/sources.list /etc/apt/sources.list.orig; \
    success=0; \
    for mirror in \
        http://mirror.tuna.tsinghua.edu.cn/ \
        http://mirrors.aliyun.com/ubuntu/ \
        http://mirrors.zju.edu.cn/ \
        http://mirrors.ustc.edu.cn/ubuntu/; do \
        mirror="${mirror%/}"; \
        sed -E \
            -e "s#http://archive.ubuntu.com/ubuntu/?#${mirror}#g" \
            -e "s#http://security.ubuntu.com/ubuntu/?#${mirror}#g" \
            -e "s#http://mirrors.tuna.tsinghua.edu.cn/ubuntu/?#${mirror}#g" \
            /etc/apt/sources.list.orig > /etc/apt/sources.list; \
        rm -rf /var/lib/apt/lists/*; \
        if apt-get update && apt-get install -y --no-install-recommends \
            gcc \
            make \
            lib32z1 \
            libc6-dev \
            xinetd; then \
            success=1; \
            break; \
        fi; \
    done; \
    test "$success" = "1"; \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 ctf && \
    useradd -u 1000 -g 1000 -m ctf

WORKDIR /home/ctf

# Every absolute path below is copied inside the Docker build container,
# following the ctf-docker-template pwn Ubuntu layout. Keep these chroot
# filesystem setup commands out of host scripts.
RUN mkdir -p /home/ctf/lib64 /home/ctf/lib/x86_64-linux-gnu /home/ctf/usr/lib && \
    (cp -L /lib64/ld-linux-x86-64.so.2 /home/ctf/lib64/ 2>/dev/null || \
        cp /lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 /home/ctf/lib64/ld-linux-x86-64.so.2) && \
    cp -a /lib/x86_64-linux-gnu/*.so* /home/ctf/lib/x86_64-linux-gnu/ 2>/dev/null || true && \
    cp -a /usr/lib/x86_64-linux-gnu /home/ctf/usr/lib/ 2>/dev/null || true

RUN mkdir -p /home/ctf/dev && \
    mknod /home/ctf/dev/null c 1 3 && \
    mknod /home/ctf/dev/zero c 1 5 && \
    mknod /home/ctf/dev/random c 1 8 && \
    mknod /home/ctf/dev/urandom c 1 9 && \
    chmod 666 /home/ctf/dev/*

RUN mkdir -p /home/ctf/bin && \
    cp /bin/sh /home/ctf/bin && \
    cp /bin/ls /home/ctf/bin && \
    cp /bin/cat /home/ctf/bin && \
    cp /usr/bin/timeout /home/ctf/bin

COPY deploy/_files/ctf.xinetd /etc/xinetd.d/ctf
COPY deploy/_files/start.sh /root/start.sh
RUN echo "Blocked by ctf_xinetd" > /etc/banner_fail

RUN chmod +x /root/start.sh

COPY deploy/src/ /tmp/src/
RUN cd /tmp/src && \
    make clean && make && \
    cp {{BINARY_NAME}} /home/ctf/{{BINARY_NAME}} && \
    rm -rf /tmp/src

RUN chown -R root:ctf /home/ctf && \
    chmod -R 750 /home/ctf && \
    touch /home/ctf/flag && \
    chmod 744 /home/ctf/flag && \
    chmod 711 /home/ctf/{{BINARY_NAME}}

CMD ["/root/start.sh"]
EXPOSE {{SERVICE_PORT}}
