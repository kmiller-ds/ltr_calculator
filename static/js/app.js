(() => {
  const payload = document.getElementById("source-data");
  const siteSelect = document.getElementById("site");
  const offerSelect = document.getElementById("offer");
  const introPrice = document.getElementById("intro_price");
  const sourceForm = document.getElementById("model-form");
  const sourceFile = document.getElementById("source_file");

  if (!payload || !siteSelect || !offerSelect || !introPrice) return;

  sourceFile?.addEventListener("change", () => {
    if (sourceFile.files.length > 0 && sourceForm) {
      const action = document.createElement("input");
      action.type = "hidden";
      action.name = "action";
      action.value = "upload_source";
      sourceForm.append(action);
      sourceForm.requestSubmit();
    }
  });

  let sourceData;
  try {
    sourceData = JSON.parse(payload.textContent);
  } catch {
    return;
  }

  const updateIntroPrice = () => {
    const price = sourceData.prices?.[siteSelect.value]?.[offerSelect.value];
    if (price !== undefined && price !== null) introPrice.value = price;
  };

  const updateOffers = () => {
    const offers = sourceData.offersBySite?.[siteSelect.value] ?? [];
    const selectedOffer = offerSelect.value;
    offerSelect.replaceChildren(
      ...offers.map((offer) => new Option(offer, offer, false, offer === selectedOffer)),
    );
    if (!offers.includes(selectedOffer) && offers.length > 0) {
      offerSelect.value = offers[0];
    }
    updateIntroPrice();
  };

  siteSelect.addEventListener("change", updateOffers);
  offerSelect.addEventListener("change", updateIntroPrice);
})();