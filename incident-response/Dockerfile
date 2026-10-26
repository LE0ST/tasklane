FROM python:3.11.14-alpine3.22

# Non-root user for bounded process execution
RUN addgroup -S oncall && adduser -S oncall -G oncall

WORKDIR /opt/tasklane

# Install minimal tools needed for process inspection
RUN apk add --no-cache curl bash git

# Copy incident-response module
COPY incident-response/ /opt/tasklane/
RUN chown -R oncall:oncall /opt/tasklane

USER oncall

EXPOSE 8001

CMD ["python", "-u", "responder.py", "8001"]
