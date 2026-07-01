## Build
docker build -t "pwn:training-hacknote" .

## Run
docker run -d -p "0.0.0.0:pub_port:9999" -h "pwn:training-hacknote" --name="training-hacknote" pwn:training-hacknote

pub_port替换成未使用的端口，建议使用较大的端口