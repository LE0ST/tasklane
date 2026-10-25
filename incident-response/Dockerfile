FROM python:3.11.14-alpine3.22

WORKDIR /opt/tasklane

# Install minimal tools needed for process inspection
RUN apk add --no-cache curl bash git

# Copy incident-response module
COPY incident-response/ /opt/tasklane/

EXPOSE 8001

CMD ["python", "-u", "responder.py", "8001"]
