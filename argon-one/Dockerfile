FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-libgpiod python3-smbus dbus-bin ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1
# Argon's scripts expect these paths; the files live in the app's data folder.
RUN ln -s /data/argon /etc/argon \
 && ln -s /data/config/argononed.conf /etc/argononed.conf \
 && ln -s /data/config/argonunits.conf /etc/argonunits.conf
COPY bin/ /usr/local/sbin/
COPY app/ /app/
