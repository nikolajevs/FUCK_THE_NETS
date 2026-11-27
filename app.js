const state={offers:[],history:{}};
const money=value=>new Intl.NumberFormat("lv-LV",{style:"currency",currency:"EUR"}).format(value);
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const formatDate=value=>{const [y,m,d]=String(value).split("-").map(Number);return new Intl.DateTimeFormat("lv-LV").format(new Date(y,m-1,d));};

function priceHistoryMarkup(offer){
  const history=state.history[offer.productId||""];
  const points=(history?.points||[]).filter(point=>Number.isFinite(point.price)).slice(-60);
  const priceLabel=offer.requiresPaldies?"Cena ar PALDIES":"Akcijas cena";
  const pointPriceLabel=offer.requiresPaldies?"cena ar PALDIES":"akcijas cena";
  if(!points.length)return '<p class="history-note">Cenu vēsture vēl nav savākta.</p>';
  const first=points[0],last=points[points.length-1];
  if(points.length===1){
    const usual=Number.isFinite(last.regularPrice)?' · Parastā cena: '+money(last.regularPrice):"";
    return '<div class="history-summary"><strong>'+money(last.price)+'</strong><span>Pirmais novērojums: '+escapeHtml(formatDate(last.date))+usual+'</span></div><p class="history-note">Pašlaik ir tikai viens mērījums. Pēc nākamajiem ikdienas atjauninājumiem šeit būs redzamas cenu izmaiņas.</p>';
  }

  const width=640,height=250,left=68,right=22,top=18,bottom=42;
  const regular=points.map(point=>Number.isFinite(point.regularPrice)?point.regularPrice:null);
  const values=[...points.map(point=>point.price),...regular.filter(Number.isFinite)];
  let min=Math.min(...values),max=Math.max(...values);
  const padding=Math.max((max-min)*.18,.05);
  min=Math.max(0,min-padding);max+=padding;
  if(max<=min)max=min+1;
  const x=index=>left+(index/(points.length-1))*(width-left-right);
  const y=value=>top+(max-value)/(max-min)*(height-top-bottom);
  const promoCoordinates=points.map((point,index)=>x(index).toFixed(1)+","+y(point.price).toFixed(1)).join(" ");
  const regularCoordinates=points.map((point,index)=>Number.isFinite(point.regularPrice)?x(index).toFixed(1)+","+y(point.regularPrice).toFixed(1):null).filter(Boolean).join(" ");
  const grid=[0,1,2,3].map(index=>{
    const value=min+(max-min)*index/3,yy=y(value);
    return '<line class="chart-grid" x1="'+left+'" y1="'+yy+'" x2="'+(width-right)+'" y2="'+yy+'"/><text class="chart-axis" x="'+(left-9)+'" y="'+(yy+4)+'" text-anchor="end">'+escapeHtml(money(value))+'</text>';
  }).join("");
  const promoCircles=points.map((point,index)=>'<circle class="chart-point promo-point" cx="'+x(index)+'" cy="'+y(point.price)+'" r="'+(index===points.length-1?5:3.5)+'"><title>'+escapeHtml(formatDate(point.date)+"+pointPriceLabel+" "+money(point.price))+'</title></circle>').join("");
  const regularCircles=points.map((point,index)=>Number.isFinite(point.regularPrice)?'<circle class="chart-point regular-point" cx="'+x(index)+'" cy="'+y(point.regularPrice)+'" r="3"><title>'+escapeHtml(formatDate(point.date)+": parastā cena "+money(point.regularPrice))+'</title></circle>':"").join("");
  const difference=last.price-first.price;
  const percent=first.price?difference/first.price*100:0;
  const trend=difference>.001?"price-up":difference<-.001?"price-down":"price-flat";
  const trendText=difference>.001?"Akcijas cena pieaugusi":difference<-.001?"Akcijas cena samazinājusies":"Akcijas cena nav mainījusies";
  const regularLine=regular.filter(Number.isFinite).length>1?'<polyline class="chart-line regular-line" points="'+regularCoordinates+'"/>':"";
  const legend='<div class="chart-legend"><span><i class="legend-swatch promo-swatch"></i>Akcijas cena</span><span><i class="legend-swatch regular-swatch"></i>Parastā cena</span></div>';
  return '<div class="history-summary"><strong>'+money(last.price)+'</strong><span>'+escapeHtml(formatDate(first.date))+' → '+escapeHtml(formatDate(last.date))+'</span><b class="'+trend+'">'+(difference>0?"+":"")+money(difference)+' ('+(percent>0?"+":"")+percent.toFixed(1)+'%)</b></div>'+legend+'<div class="chart-wrap"><svg class="price-chart" viewBox="0 0 '+width+' '+height+'" role="img" aria-label="Akcijas un parastās cenas izmaiņas laikā">'+grid+regularLine+'<polyline class="chart-line" points="'+promoCoordinates+'"/>'+regularCircles+promoCircles+'<text class="chart-date" x="'+left+'" y="'+(height-12)+'">'+escapeHtml(formatDate(first.date))+'</text><text class="chart-date" x="'+(width-right)+'" y="'+(height-12)+'" text-anchor="end">'+escapeHtml(formatDate(last.date))+'</text></svg></div><p class="history-note">'+trendText+'. Dati tiek vākti no ikdienas Maxima piedāvājumu pārbaudēm, nevis no pilna veikala cenu arhīva.</p>';
}

function render(){
  const query=document.querySelector("#search").value.trim().toLocaleLowerCase("lv");
  const city=document.querySelector("#city").value;
  const sort=document.querySelector("#sort").value;
  const items=state.offers.filter(offer=>(!city||offer.city===city)&&[offer.name,offer.brand,offer.store].some(value=>String(value??"").toLocaleLowerCase("lv").includes(query)));
  items.sort((a,b)=>sort==="price"?(a.price??Infinity)-(b.price??Infinity):sort==="name"?a.name.localeCompare(b.name,"lv"):(b.discountPercent??0)-(a.discountPercent??0));
  document.querySelector("#count").textContent=items.length+" piedāvājumi";
  document.querySelector("#offers").innerHTML=items.map(offer=>{
    const historyId=offer.productId||"";
    const storeLabel=Array.isArray(offer.storeFormats)&&offer.storeFormats.length?"MAXIMA "+offer.storeFormats.join(", "):([offer.city,offer.store].filter(Boolean).join(" · ")||"Vieta nav norādīta");
    const validity=offer.validUntil?formatDate(offer.validUntil):"nav norādīts";
    return `<article class="card">
      <div class="card-image">${offer.image?'<img src="'+escapeHtml(offer.image)+'" alt="" style="max-width:100%;max-height:100%;object-fit:contain">':'%'}</div>
      <div class="card-body">
        ${Number.isFinite(offer.discountPercent)?'<span class="badge">-'+escapeHtml(offer.discountPercent)+'%</span>':''}
        <h3>${escapeHtml(offer.name)}</h3><div class="meta">${escapeHtml([offer.brand,offer.unit].filter(Boolean).join(" · "))}</div>
        <div class="price-row"><span class="price">${Number.isFinite(offer.price)?money(offer.price):"Cena nav norādīta"}</span>${Number.isFinite(offer.regularPrice)?'<span class="old-price">'+money(offer.regularPrice)+'</span>':''}</div>
        <div class="card-foot"><span>${escapeHtml(storeLabel)}</span><span>${offer.paldis?"PALDIES":""}</span></div>
        <div class="card-foot"><span>Derīgs līdz: ${escapeHtml(validity)}</span><a href="${escapeHtml(offer.source||"#")}" target="_blank" rel="noopener">Avots ↗</a></div>
        <button class="history-toggle" type="button" data-history-toggle="${escapeHtml(historyId)}" aria-expanded="false">Cenu vēsture <span aria-hidden="true">⌄</span></button>
        <div class="history-panel" id="history-${escapeHtml(historyId)}" hidden>${priceHistoryMarkup(offer)}</div>
      </div>
    </article>`;
  }).join("");
  document.querySelector("#empty").hidden=items.length!==0;
}

async function init(){
  try{
    const response=await fetch("data/offers.json",{cache:"no-store"});
    if(!response.ok)throw new Error("Cannot load offers");
    const data=await response.json();
    state.offers=Array.isArray(data.offers)?data.offers:[];
    try{
      const historyResponse=await fetch("data/price-history.json",{cache:"no-store"});
      if(historyResponse.ok){
        const historyData=await historyResponse.json();
        state.history=historyData.products||{};
      }
    }catch(error){console.warn("Price history unavailable",error);}
    const cities=[...new Set(state.offers.map(offer=>offer.city).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"lv"));
    document.querySelector("#city").innerHTML='<option value="">Visas pilsētas</option>'+cities.map(city=>'<option>'+escapeHtml(city)+'</option>').join("");
    document.querySelector("#updated").textContent=data.updatedAt?"Atjaunots: "+data.updatedAt:"";
  }catch(error){
    console.error(error);
    document.querySelector("#empty").hidden=false;
    document.querySelector("#empty h3").textContent="Neizdevās ielādēt piedāvājumus";
  }
  render();
}

document.querySelector("#offers").addEventListener("click",event=>{
  const button=event.target.closest("[data-history-toggle]");
  if(!button)return;
  const panel=document.querySelector("#history-"+CSS.escape(button.dataset.historyToggle));
  if(!panel)return;
  const open=panel.hidden;
  panel.hidden=!open;
  button.setAttribute("aria-expanded",String(open));
  button.querySelector("span").textContent=open?"⌃":"⌄";
});
["search","city","sort"].forEach(id=>document.querySelector("#"+id).addEventListener(id==="search"?"input":"change",render));
init();
