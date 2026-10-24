"""Optional OpenTelemetry request instrumentation. Enabled only with TASKLANE_OTEL_ENABLED=true."""
import json
import logging
import os
import time

logger = logging.getLogger("tasklane.telemetry")


def enable_telemetry(app, tracer_provider=None, meter_provider=None, logger_provider=None):
    """Attach telemetry middleware when explicitly enabled; fail open on misconfiguration."""
    if os.getenv("TASKLANE_OTEL_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return False
    try:
        from opentelemetry import trace, metrics
        from opentelemetry.trace import SpanKind, StatusCode, Status
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
    if tracer_provider is None:
        if not endpoint or not endpoint.startswith(("http://", "https://")):
            logger.warning("OpenTelemetry disabled: TASKLANE_OTEL_ENDPOINT is not configured")
            return False

    resource = Resource.create({"service.name": "tasklane-api", "deployment.environment": "local"})

    if tracer_provider is None:
        traces = TracerProvider(resource=resource)
        traces.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces")))
        trace.set_tracer_provider(traces)
    else:
        traces = tracer_provider

    if meter_provider is None:
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint + "/v1/metrics"),
            export_interval_millis=5000,
        )
        mp = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(mp)
    else:
        mp = meter_provider

    if logger_provider is None:
        lp = LoggerProvider(resource=resource)
        lp.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint + "/v1/logs"))
        )
    else:
        lp = logger_provider

    otel_handler = LoggingHandler(level=logging.INFO, logger_provider=lp)
    otel_handler.setFormatter(logging.Formatter("%(message)s"))
    if not any(isinstance(h, LoggingHandler) for h in logger.handlers):
        logger.addHandler(otel_handler)
    logger.setLevel(logging.INFO)

    meter = mp.get_meter("tasklane.http")
    count = meter.create_counter("tasklane_http_requests", unit="1")
    duration = meter.create_histogram("tasklane_http_duration", unit="ms")
    tracer = traces.get_tracer("tasklane.http")

    @app.middleware("http")
    async def instrument_requests(request, call_next):
        route = request.scope.get("route")
        method = request.method.upper()
        started = time.perf_counter()
        with tracer.start_as_current_span(
            "HTTP " + method,
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            status_code = 500
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            except Exception:
                status_code = 500
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                route = request.scope.get("route") or route
                template = getattr(route, "path", None)
                if template not in {"/api/tasks", "/api/tasks/{task_id}", "/health", "/api/health"}:
                    template = "other"
                safe_method = method if method in {"GET", "POST", "PATCH", "DELETE", "OPTIONS"} else "OTHER"
                attrs = {
                    "http.request.method": safe_method,
                    "http.route": template,
                    "http.response.status_code": int(status_code),
                }
                elapsed = (time.perf_counter() - started) * 1000
                span.set_attributes(attrs)
                count.add(1, attrs)
                duration.record(elapsed, attrs)
                ctx = span.get_span_context()
                logger.info(
                    json.dumps({
                        "event": "http_request",
                        "method": safe_method,
                        "route": template,
                        "status_code": status_code,
                        "duration_ms": round(elapsed, 2),
                        "trace_id": format(ctx.trace_id, "032x"),
                        "span_id": format(ctx.span_id, "016x"),
                    })
                )

    return True
