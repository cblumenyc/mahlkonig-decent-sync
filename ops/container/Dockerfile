FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN groupadd --gid 10001 syncapp && useradd --uid 10001 --gid syncapp --no-create-home --shell /usr/sbin/nologin syncapp \
    && mkdir /data && chown syncapp:syncapp /data && chmod 700 /data && command -v setpriv
WORKDIR /app
COPY app/requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
COPY app/ /app/
COPY launch.py healthcheck.py /app/
COPY --chmod=755 runtime.sh /usr/local/bin/sync-runtime
USER 0:0
VOLUME ["/data"]
EXPOSE 80
ENTRYPOINT ["/usr/local/bin/sync-runtime"]
CMD ["python", "/app/launch.py"]
