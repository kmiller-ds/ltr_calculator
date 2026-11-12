from __future__ import annotations
import traceback
import os
import secrets
import time
from io import BytesIO
from typing import Any

import plotly.graph_objects as go
from flask import Flask, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from model import (
    DATA_CONFIG,
    MODEL_DEFAULTS,
    available_options,
    load_uploaded_source,
    load_source,
    offer_price_map,
    run_model,
)

UPLOAD_CACHE_TTL = 30 * 60
MAX_UPLOAD_SIZE = 16 * 1024 * 1024


FIELD_TYPES = {
    "grace_days": int,
    "pct_nonpay": float,
    "blended_step_up": float,
    "sales_lift": float,
    "monthly_churn": float,
}
OPTIONAL_FLOAT_FIELDS = {"step_override", "cpa", "intro_price"}
FORM_FIELDS = (*FIELD_TYPES, *OPTIONAL_FLOAT_FIELDS)


def parse_config(form_data: Any, defaults: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    config = {"offer": defaults["offer"], "site": defaults["site"]}
    errors: list[str] = []
    for field in ("offer", "site"):
        config[field] = form_data.get(field, "").strip() or defaults[field]

    for field, converter in FIELD_TYPES.items():
        raw = form_data.get(field, "").strip()
        if not raw:
            config[field] = MODEL_DEFAULTS[field]
            continue
        try:
            value = converter(raw)
            if value < 0:
                errors.append(f"{field.replace('_', ' ').title()} cannot be negative.")
            config[field] = value
        except (TypeError, ValueError, OverflowError):
            errors.append(f"{field.replace('_', ' ').title()} must be a valid number.")

    for field in OPTIONAL_FLOAT_FIELDS:
        raw = form_data.get(field, "").strip()
        if not raw:
            config[field] = None
            continue
        try:
            value = float(raw)
            if value < 0 or value == float("inf") or value != value:
                errors.append(f"{field.replace('_', ' ').title()} must be a non-negative number.")
            config[field] = value
        except (TypeError, ValueError, OverflowError):
            errors.append(f"{field.replace('_', ' ').title()} must be a valid number.")
    return config, errors


def form_values(form_data: Any, defaults: dict[str, Any]) -> dict[str, Any]:
    values = {**MODEL_DEFAULTS, **defaults, "step_override": "", "cpa": ""}
    for field in FORM_FIELDS:
        if field in form_data:
            values[field] = form_data.get(field, "")
    return values


def make_plot(result, column: str, title: str) -> str:
    figure = go.Figure(go.Scatter(
        x=[result["Year"], result["Week"]],
        y=result[column],
        mode="lines",
        name=title,
        hovertemplate=f"Year %{{x[0]}}<br>Week %{{x[1]}}<br>{title}: %{{y}}<extra></extra>",
    ))
    figure.update_layout(
        title=title,
        height=330,
        margin={"l": 24, "r": 20, "t": 52, "b": 24},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "Arial, sans-serif", "color": "#172b4d"},
        hovermode="x unified",
    )
    figure.update_xaxes(type="multicategory", tickangle=-90, gridcolor="#e1e8ef")
    figure.update_yaxes(gridcolor="#e1e8ef", zerolinecolor="#c7d2de")
    return figure.to_html(full_html=False, include_plotlyjs="cdn")


def create_app(source=None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "local-development-only-change-before-deploy")
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE
    app.config["SOURCE_DATA"] = source
    app.config["UPLOADED_SOURCES"] = {}

    def get_source():
        if app.config["SOURCE_DATA"] is None:
            app.config["SOURCE_DATA"] = load_source(DATA_CONFIG)
        return app.config["SOURCE_DATA"]

    def get_uploaded_source():
        cache = app.config["UPLOADED_SOURCES"]
        now = time.monotonic()
        for upload_id, entry in list(cache.items()):
            if now - entry["last_used"] > UPLOAD_CACHE_TTL:
                del cache[upload_id]

        upload_id = session.get("uploaded_source_id")
        entry = cache.get(upload_id)
        if entry is None:
            return None

        entry["last_used"] = now
        return entry["source"]

    @app.route("/", methods=["GET", "POST"])
    def index():
        if request.method == "GET" and request.args.get("reset") == "1":
            session.pop("model_config", None)
            session.pop("uploaded_source_id", None)
            session.pop("uploaded_source_name", None)
            return redirect(url_for("index"))

        errors: list[str] = [request.args["error"]] if "error" in request.args else []
        notices: list[str] = []
        result = None
        summary = None
        plots = {}
        options = {"offers": [], "sites": []}
        source_data = {"offersBySite": {}, "prices": {}}
        source = None
        uploaded_new_source = False

        try:
            action = request.form.get("action") if request.method == "POST" else None
            uploaded_file = request.files.get("source_file") if request.method == "POST" else None
            if uploaded_file and uploaded_file.filename:
                if action == "upload_source":
                    try:
                        source = load_uploaded_source(uploaded_file.read(), uploaded_file.filename)
                    except Exception as exc:
                        errors.append(f"Could not validate uploaded source: {exc}")
                        source = get_uploaded_source()
                        if source is None:
                            source = get_source()
                    else:
                        uploaded_new_source = True
                        upload_id = session.get("uploaded_source_id") or secrets.token_urlsafe(24)
                        session["uploaded_source_id"] = upload_id
                        session["uploaded_source_name"] = uploaded_file.filename
                        app.config["UPLOADED_SOURCES"][upload_id] = {
                            "source": source,
                            "last_used": time.monotonic(),
                        }
                        notices.append(f"{uploaded_file.filename} uploaded and validated.")
                else:
                    errors.append("Upload and validate the selected source file before running a forecast.")
                    source = get_uploaded_source()
                    if source is None:
                        source = get_source()
            else:
                had_uploaded_source = bool(session.get("uploaded_source_id"))
                source = get_uploaded_source()
                if source is None:
                    if had_uploaded_source:
                        session.pop("uploaded_source_id", None)
                        session.pop("uploaded_source_name", None)
                        errors.append("The uploaded source expired. Upload it again to run a forecast.")
                    else:
                        source = get_source()

            if source is not None:
                options = available_options(source)
                prices = offer_price_map(source)
                source_data = {
                    "offersBySite": {
                        site: sorted(group["Offer"].dropna().astype(str).unique())
                        for site, group in source.groupby("CM Site Name")
                    },
                    "prices": prices,
                }
        except Exception as exc:
            traceback.print_exc()
            errors.append(f"Could not load source data: {exc}")

        source_has_total = source is not None and source["CM Site Name"].eq("Total").any()
        default_site = (
            "Total" if source_has_total
            else next((site for site in options["sites"] if site != "Total"), "")
        )
        available_offers = source_data["offersBySite"].get(default_site, options["offers"])
        default_offer = available_offers[0] if available_offers else ""
        defaults = {**MODEL_DEFAULTS, "site": default_site, "offer": default_offer}
        defaults["intro_price"] = source_data["prices"].get(default_site, {}).get(default_offer, "")

        form_data = request.form.copy() if request.method == "POST" else {}
        if uploaded_new_source:
            if form_data.get("site") not in options["sites"]:
                form_data["site"] = default_site
            valid_offers = source_data["offersBySite"].get(form_data.get("site"), [])
            if form_data.get("offer") not in valid_offers:
                form_data["offer"] = valid_offers[0] if valid_offers else ""
            form_data["intro_price"] = ""
        config, parse_errors = parse_config(form_data, defaults)
        errors.extend(parse_errors)
        values = form_values(form_data, {**defaults, **config})

        run_requested = request.method == "POST" and request.form.get("action") != "upload_source"
        if source is not None and run_requested and not errors:
            valid_offers = source_data["offersBySite"].get(config["site"], [])
            if config["site"] not in options["sites"]:
                errors.append("Select a site from the available source data.")
            elif config["offer"] not in valid_offers:
                errors.append("Select an offer available for the chosen site.")

        if source is not None and run_requested and not errors:
            try:
                result = run_model(config, source)
                session["model_config"] = config
                summary = {
                    "weeks": len(result),
                    "ending_retention": result.iloc[-1]["Retention"],
                    "total_net_revenue": result.iloc[-1]["Total Net Revenue"],
                    "total_gross_revenue": result["Gross Incremental Revenue"].sum(),
                }
                plots = {
                    "retention": make_plot(result, "Retention", "Retention"),
                    "net": make_plot(result, "Total Net Revenue", "Cumulative net revenue"),
                    "units": make_plot(result, "Units", "Units"),
                }
            except Exception as exc:
                traceback.print_exc()
                errors.append(f"The model could not run: {exc}")

        if not values.get("intro_price"):
            values["intro_price"] = source_data["prices"].get(values.get("site"), {}).get(values.get("offer"), "")

        return render_template(
            "index.html",
            defaults=defaults,
            values=values,
            options=options,
            source_data=source_data,
            errors=errors,
            notices=notices,
            summary=summary,
            plots=plots,
            rows=result.to_dict(orient="records") if result is not None else None,
        )

    @app.route("/download")
    def download():
        config = session.get("model_config")
        if not config:
            return redirect(url_for("index"))
        source = get_uploaded_source()
        if source is None:
            if session.get("uploaded_source_id"):
                return redirect(url_for(
                    "index",
                    error="The uploaded source expired. Upload it again to run or download a forecast.",
                ))
            source = get_source()
        result = run_model(config, source)
        output = result.to_csv(index=False).encode("utf-8")
        return send_file(
            BytesIO(output),
            mimetype="text/csv",
            as_attachment=True,
            download_name="ltr_output.csv",
        )

    @app.errorhandler(RequestEntityTooLarge)
    def handle_large_upload(error):
        return f"{error.description} Source files must be 16 MB or smaller.", 413

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5001")), debug=True, use_reloader=False)