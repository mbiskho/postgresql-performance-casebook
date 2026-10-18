# The runner shares the server's base image so pgbench and psql match the
# server version exactly. Python and psycopg 3 come from Debian packages.
FROM postgres:16.4-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-psycopg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /casebook
COPY casebook/ /casebook/casebook/
COPY cases/ /casebook/cases/
COPY sql/ /casebook/sql/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python3", "-m", "casebook"]
CMD ["--help"]
