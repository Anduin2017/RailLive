FROM python:3.13-slim AS build
WORKDIR /src
COPY backend/ backend/
COPY tools/prepare.py tools/
COPY tools/snapshot/ tools/snapshot/
COPY web/ web/
RUN PYTHONDONTWRITEBYTECODE=1 python tools/prepare.py --output /opt/rails --snapshot /snapshot

FROM python:3.13-slim
WORKDIR /opt/rails
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 RAILS_BIND=0.0.0.0 RAILS_PORT=8080
COPY --from=build /opt/rails/ ./
COPY LICENSE THIRD_PARTY.md /usr/share/doc/rails/
COPY licenses/ /usr/share/doc/rails/licenses/
USER 65534:65534
EXPOSE 8080
CMD ["python", "serve.py"]
