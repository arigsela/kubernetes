FROM ubuntu:20.04

# Avoid interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && \
    apt-get install -y \
        awscli \
        curl \
        ca-certificates \
        && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the update script
COPY update-route53.sh /usr/local/bin/update-route53.sh
RUN chmod +x /usr/local/bin/update-route53.sh

# Set entrypoint
ENTRYPOINT ["/usr/local/bin/update-route53.sh"]