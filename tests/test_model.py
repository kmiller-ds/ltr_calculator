import unittest

import pandas as pd

from app import create_app
from model import MODEL_DEFAULTS, normalize_source_data, offer_price_map, run_model


def prepared_source(intro_price=3.5):
    return pd.DataFrame([{
        "CM Site Name": "Example Site",
        "Offer": "Test Offer",
        "Offer Length": 5,
        "Drop 1": 0.1,
        "Drop 2": 0.08,
        "Drop 3": 0.06,
        "Drop 4": 0.04,
        "Intro Price": intro_price,
    }])


class SourceDataTests(unittest.TestCase):
    def test_explicit_source_price_is_preserved(self):
        source = normalize_source_data(prepared_source(4.75))
        self.assertEqual(source.iloc[0]["Intro Price"], 4.75)
        self.assertEqual(offer_price_map(source)["Example Site"]["Test Offer"], 4.75)

    def test_legacy_sheet_gets_compatibility_prices(self):
        raw = pd.DataFrame([[
            "Example Site", -0.1, -0.08, -0.06, -0.04,
            "annual block", -0.12, -0.09, -0.07, -0.05,
        ]], columns=[f"column_{index}" for index in range(10)])
        raw.loc[len(raw)] = [None] * 10

        source = normalize_source_data(raw)
        monthly = source[source["Offer"] == "Monthly Retention"].iloc[0]
        annual = source[source["Offer"] == "Annual Retention"].iloc[0]
        self.assertEqual(monthly["Intro Price"], 1.0)
        self.assertEqual(annual["Intro Price"], 8.25)
        self.assertEqual(monthly["Drop 1"], 0.1)
        self.assertEqual(annual["Drop 1"], 0.12)
        self.assertEqual(len(source), 2)


class ForecastTests(unittest.TestCase):
    def test_model_returns_five_years_and_uses_source_price(self):
        source = prepared_source(4.75)
        result = run_model({
            **MODEL_DEFAULTS,
            "site": "Example Site",
            "offer": "Test Offer",
            "intro_price": None,
        }, source)

        self.assertEqual(len(result), 260)
        self.assertAlmostEqual(result.iloc[0]["Weekly Rate"], 4.75 * (7 / (365.25 / 12)))
        self.assertEqual(result.iloc[-1]["Year"], 5)

    def test_ui_renders_source_defaults_and_separate_assets(self):
        app = create_app(prepared_source(4.75))
        app.testing = True
        client = app.test_client()
        response = client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Example Site", response.data)
        self.assertIn(b"Test Offer", response.data)
        self.assertIn(b'value="4.75"', response.data)
        self.assertIn(b"<h2>Projection</h2>", response.data)
        self.assertIn(b"Run forecast to see cumulative net revenue.", response.data)
        self.assertIn(b"Run forecast to see retention.", response.data)
        self.assertIn(b"Run forecast to see units.", response.data)
        self.assertIn(b"Run forecast to see the weekly results.", response.data)
        self.assertNotIn(b'href="/download"', response.data)

        forecast_response = client.post("/", data={
            "site": "Example Site",
            "offer": "Test Offer",
        })
        self.assertEqual(forecast_response.status_code, 200)
        self.assertIn(b"Ending retention", forecast_response.data)
        self.assertIn(b"Projection <span>Example Site / Test Offer</span>", forecast_response.data)
        self.assertIn(b"Weekly results", forecast_response.data)
        self.assertIn(b'href="/download"', forecast_response.data)
        self.assertNotIn(b"Run forecast to see", forecast_response.data)

        for asset in ("/static/css/app.css", "/static/js/app.js"):
            asset_response = client.get(asset)
            self.assertEqual(asset_response.status_code, 200)
            if asset.endswith("app.css"):
                self.assertIn(
                    b".chart-grid { display: grid; grid-template-columns: minmax(0, 1fr);",
                    asset_response.data,
                )
            asset_response.close()


if __name__ == "__main__":
    unittest.main()