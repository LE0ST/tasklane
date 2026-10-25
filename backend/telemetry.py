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

    handler_added = None
    created_traces = None
    created_mp = None
    created_lp = None

    try:
        resource = Resource.create({"service.name": "tasklane-api", "deployment.environment": "local"})

        if tracer_provider is None:
            created_traces = TracerProvider(resource=resource)
            span_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces"))
            created_traces.add_span_processor(span_processor)
            traces = created_traces
        else:
            traces = tracer_provider

        if meter_provider is None:
            reader = PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=endpoint + "/v1/metrics"),
                export_interval_millis=5000,
            )
            created_mp = MeterProvider(resource=resource, metric_readers=[reader])
            mp = created_mp
        else:
            mp = meter_provider

        if logger_provider is None:
            created_lp = LoggerProvider(resource=resource)
            created_lp.add_log_record_processor(
                BatchLogRecordProcessor(OTLPLogExporter(endpoint=endpoint + "/v1/logs"))
            )
            lp = created_lp
        else:
            lp = logger_provider

        otel_handler = LoggingHandler(level=logging.INFO, logger_provider=lp)
        otel_handler.setFormatter(logging.Formatter("%(message)s"))
        if not any(isinstance(h, LoggingHandler) for h in logger.handlers):
            logger.addHandler(otel_handler)
            handler_added = otel_handler
        logger.setLevel(logging.INFO)

        if tracer_provider is None:
            trace.set_tracer_provider(traces)
        if meter_provider is None:
            metrics.set_meter_provider(mp)

        meter = mp.get_meter("tasklane.http")
        count = meter.create_counter("tasklane_http_requests", unit="1")
        duration = meter.create_histogram("tasklane_http_duration", unit="ms")
        # The first error must be observable as an increase, not as the first-ever sample.
        # Precreate only the bounded 503 series in the opt-in local incident environment.
        if os.getenv("TASKLANE_INCIDENT_DRILL_ENABLED") == "local-only":
            count.add(0, {
                "http.request.method": "POST",
                "http.route": "/api/tasks",
                "http.response.status_code": 503,
            })
        tracer = traces.get_tracer("tasklane.http")

    except Exception:
        if handler_added and handler_added in logger.handlers:
            logger.removeHandler(handler_added)
        if created_traces:
            try:
                created_traces.shutdown()
            except Exception:
                pass
        if created_mp:
            try:
                created_mp.shutdown()
            except Exception:
                pass
        if created_lp:
            try:
                created_lp.shutdown()
            except Exception:
                pass
        logger.warning(
            "OpenTelemetry disabled: initialization failed; continuing with telemetry disabled"
        )
        return False

    @app.middleware("http")
    async def instrument_requests(request, call_next):
        route = request.scope.get("route")
        raw_method = request.method.upper()
        safe_method = raw_method if raw_method in {"GET", "POST", "PATCH", "DELETE", "OPTIONS"} else "OTHER"
        span_name = "HTTP " + safe_method
        started = time.perf_counter()
        with tracer.start_as_current_span(
            span_name,
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
                # Locally injected 503 short-circuits routing; still use the known static API template.
                if template is None and request.url.path == "/api/tasks" and safe_method == "POST":
                    template = "/api/tasks"
                if template not in {"/api/tasks", "/api/tasks/{task_id}", "/health", "/api/health"}:
                    template = "other"
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
