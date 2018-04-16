FROM mongo:3.4

RUN apt-get update \
	&& apt-get install -y \
		python \
		python-dev \
		gcc \
		curl \
		libffi-dev \
		libssl-dev \
                unzip \
	&& rm -rf /var/lib/apt/lists/*

# get Python drivers MongoDB, Consul, and Manta
RUN curl -Ls -o get-pip.py https://bootstrap.pypa.io/get-pip.py && \
	python get-pip.py && \
	pip install \
		PyMongo==3.4.0 \
		python-Consul==0.7.0 \
		manta==2.5.0 \
		mock==2.0.0

# Add consul agent
RUN export CONSUL_VERSION=1.0.6 \
    && export CONSUL_CHECKSUM=bcc504f658cef2944d1cd703eda90045e084a15752d23c038400cf98c716ea01 \
    && curl --retry 7 --fail -vo /tmp/consul.zip "https://releases.hashicorp.com/consul/${CONSUL_VERSION}/consul_${CONSUL_VERSION}_linux_amd64.zip" \
    && echo "${CONSUL_CHECKSUM}  /tmp/consul.zip" | sha256sum -c \
    && unzip /tmp/consul -d /usr/local/bin \
    && rm /tmp/consul.zip \
    && mkdir -p /opt/consul/config

# Add ContainerPilot and set its configuration file path
ENV CONTAINERPILOT_VER 3.7.0
ENV CONTAINERPILOT /etc/containerpilot.json5
RUN export CONTAINERPILOT_CHECKSUM=b10b30851de1ae1c095d5f253d12ce8fe8e7be17 \
    && curl -Lso /tmp/containerpilot.tar.gz \
        "https://github.com/joyent/containerpilot/releases/download/${CONTAINERPILOT_VER}/containerpilot-${CONTAINERPILOT_VER}.tar.gz" \
    && echo "${CONTAINERPILOT_CHECKSUM}  /tmp/containerpilot.tar.gz" | sha1sum -c \
    && tar zxf /tmp/containerpilot.tar.gz -C /usr/local/bin \
    && rm /tmp/containerpilot.tar.gz

# add stopping timeouts for MongoDB
ENV MONGO_SECONDARY_CATCHUP_PERIOD 8
ENV MONGO_STEPDOWN_TIME 60
ENV MONGO_ELECTION_TIMEOUT 30

# Configure ContainerPilot and Mongo
COPY etc/* /etc/
COPY bin/* /usr/local/bin/

ENTRYPOINT ["containerpilot"]
