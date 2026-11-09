(() => {
  const payload = document.getElementById("source-data");
  const siteSelect = document.getElementById("site");
  const offerSelect = document.getElementById("offer");
  const introPrice = document.getElementById("intro_price");

  if (!payload || !siteSelect || !offerSelect || !introPrice) return;

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