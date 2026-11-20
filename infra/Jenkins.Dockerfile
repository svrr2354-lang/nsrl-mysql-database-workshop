FROM jenkins/jenkins:lts-jdk21
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv default-mysql-client curl ca-certificates git openssh-client \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/python && /opt/python/bin/pip install --no-cache-dir \
    boto3 pymysql cryptography flask PyYAML awscli
ENV PATH="/opt/python/bin:${PATH}"
RUN mkdir -p /opt/rds && curl -fsSL https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o /opt/rds/global-bundle.pem
RUN jenkins-plugin-cli --plugins workflow-aggregator git credentials-binding ssh-credentials timestamper
COPY infra/jenkins-init.groovy /usr/share/jenkins/ref/init.groovy.d/workshop.groovy
ENV JAVA_OPTS="-Djenkins.install.runSetupWizard=false"
USER jenkins
