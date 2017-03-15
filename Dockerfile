FROM mongo:3.4

RUN apt-get update \
	&& apt-get install -y \
		python \
		python-dev \
		gcc \
		curl \
		netcat \
		libffi-dev \
		libssl-dev \
                unzip \
	&& rm -rf /var/lib/apt/lists/*

# get Python drivers MongoDB, Consul, and Manta
RUN curl -Ls -o get-pip.py https://bootstrap.pypa.io/get-pip.py && \
	python get-pip.py && \
	pip install \
		PyMongo==3.2.2 \
		python-Consul==0.4.7 \
		manta==2.5.0 \
		mock==2.0.0

# Add consul agent
RUN export CONSUL_VERSION=0.7.5 \
    && export CONSUL_CHECKSUM=40ce7175535551882ecdff21fdd276cef6eaab96be8a8260e0599fadb6f1f5b8 \
    && curl --retry 7 --fail -vo /tmp/consul.zip "https://releases.hashicorp.com/consul/${CONSUL_VERSION}/consul_${CONSUL_VERSION}_linux_amd64.zip" \
    && echo "${CONSUL_CHECKSUM}  /tmp/consul.zip" | sha256sum -c \
    && unzip /tmp/consul -d /usr/local/bin \
    && rm /tmp/consul.zip \
    && mkdir -p /opt/consul/config

# Add ContainerPilot and set its configuration file path
ENV CONTAINERPILOT_VER 2.7.0
ENV CONTAINERPILOT file:///etc/containerpilot.json
RUN export CONTAINERPILOT_CHECKSUM=687f7d83e031be7f497ffa94b234251270aee75b \
    && curl -Lso /tmp/containerpilot.tar.gz \
        "https://github.com/joyent/containerpilot/releases/download/${CONTAINERPILOT_VER}/containerpilot-${CONTAINERPILOT_VER}.tar.gz" \
    && echo "${CONTAINERPILOT_CHECKSUM}  /tmp/containerpilot.tar.gz" | sha1sum -c \
    && tar zxf /tmp/containerpilot.tar.gz -C /usr/local/bin \
    && rm /tmp/containerpilot.tar.gz

# add stopping timeouts for MongoDB
ENV MONGO_SECONDARY_CATCHUP_PERIOD 8
ENV MONGO_STEPDOWN_TIME 60
ENV MONGO_ELECTION_TIMEOUT 30

# configure ContainerPilot and MySQL
COPY etc/* /etc/
COPY bin/* /usr/local/bin/

ENTRYPOINT ["containerpilot", "mongod", "--config", "/etc/mongod.conf"]

CMD ["--replSet=joyent"]
