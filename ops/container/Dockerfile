FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 sync && useradd --uid 10001 --gid sync --no-create-home --shell /usr/sbin/nologin sync \
    && mkdir /data && chown sync:sync /data && chmod 700 /data
WORKDIR /app
COPY app/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
COPY app/ /app/
COPY launch.py healthcheck.py /app/
USER 10001:10001
VOLUME ["/data"]
EXPOSE 80
ENTRYPOINT ["python", "/app/launch.py"]
