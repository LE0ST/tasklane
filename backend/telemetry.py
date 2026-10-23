"""Optional OpenTelemetry request instrumentation. Enabled only with TASKLANE_OTEL_ENABLED=true."""
import json
import logging
import os
import time

logger = logging.getLogger("tasklane.telemetry")


def enable_telemetry(app):
    """Attach telemetry middleware when explicitly enabled; fail open on misconfiguration."""
    if os.getenv("TASKLANE_OTEL_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return False
    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    except ImportError:
        logger.warning("OpenTelemetry disabled: optional SDK packages are missing")
        return False

    endpoint = os.getenv("TASKLANE_OTEL_ENDPOINT", "").rstrip("/")
    if not endpoint or not endpoint.startswith(("http://", "https://")):
        logger.warning("OpenTelemetry disabled: TASKLANE_OTEL_ENDPOINT is not configured")
        return False

    resource = Resource.create({"service.name": "tasklane-api", "deployment.environment": "local"})
    traces = TracerProvider(resource=resource)
    traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces")))
    trace.set_tracer_provider(traces)
    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint + "/v1/metrics"),
        export_interval_millis=5000,
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint + "/v1/logs"))
    )
    otel_handler = LoggingHandler(level=logging.INFO, logger_provider=log_provider)
    otel_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(otel_handler)
    logger.setLevel(logging.INFO)
    meter = metrics.get_meter("tasklane.http")
    count = meter.create_counter("tasklane_http_requests", unit="1")
    duration = meter.create_histogram("tasklane_http_duration", unit="ms")
    tracer = trace.get_tracer("tasklane.http")

    @app.middleware("http")
    async def instrument_requests(request, call_next):
        route = request.scope.get("route")
        # Starlette may populate scope route only after call_next; use only a safe
        # static match from registered route templates, never raw request URLs.
        method = request.method.upper()
        started = time.perf_counter()
        with tracer.start_as_current_span("HTTP " + method) as span:
            status = 500
            try:
                response = await call_next(request)
                status = response.status_code
                return response
            finally:
                route = request.scope.get("route") or route
                template = getattr(route, "path", None)
                if template not in {"/api/tasks", "/api/tasks/{task_id}", "/health", "/api/health"}:
                    template = "other"
                attrs = {"http.request.method": method if method in {"GET","POST","PATCH","DELETE","OPTIONS"} else "OTHER",
                         "http.route": template, "http.response.status_code": int(status)}
                elapsed = (time.perf_counter() - started) * 1000
                span.set_attributes(attrs)
                count.add(1, attrs)
                duration.record(elapsed, attrs)
                ctx = span.get_span_context()
                logger.info(json.dumps({"event":"http_request", "method":attrs["http.request.method"],
                    "route":template,"status_code":status,
                    "duration_ms":round(elapsed,2),
                    "trace_id":format(ctx.trace_id,"032x"),
                    "span_id":format(ctx.span_id,"016x")}))
    return True
