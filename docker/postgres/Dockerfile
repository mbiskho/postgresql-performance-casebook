# PostgreSQL 16 with the extensions the casebook relies on.
# pg_stat_statements, auto_explain and pg_trgm ship with core. HypoPG comes from
# the PGDG apt repository the official image already trusts; installing it also
# moves the server to the newest 16.x minor release in that repository, which
# is what `casebook check` and the reports print as the server version.
FROM postgres:16.4-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-hypopg \
 && rm -rf /var/lib/apt/lists/*

COPY docker/postgres/postgresql.conf /etc/postgresql/postgresql.conf
COPY docker/postgres/initdb/ /docker-entrypoint-initdb.d/

CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
