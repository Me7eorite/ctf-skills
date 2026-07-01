FROM ubuntu:20.04

RUN sed -i "s/http:\/\/archive.ubuntu.com/http:\/\/mirrors.tuna.tsinghua.edu.cn/g" /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        make \
        libc6-dev \
        xinetd

RUN groupadd -g 1000 ctf && \
    useradd -u 1000 -g 1000 -m ctf

WORKDIR /home/ctf

RUN cp -R /usr/lib* /home/ctf

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

COPY ./_files/ctf.xinetd /etc/xinetd.d/ctf
COPY ./_files/start.sh /root/start.sh
RUN echo "Blocked by ctf_xinetd" > /etc/banner_fail

RUN chmod +x /root/start.sh

COPY ./src/ /tmp/src/
RUN cd /tmp/src && \
    make clean && make && \
    cp pwn /home/ctf/pwn && \
    chmod 755 /home/ctf/pwn && \
    rm -rf /tmp/src

RUN chown -R ctf:ctf /home/ctf && \
    chmod -R 777 /home/ctf

CMD ["/root/start.sh"]
EXPOSE {{SERVICE_PORT}}
