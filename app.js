const state={products:[],history:{}};
const money=value=>new Intl.NumberFormat("lv-LV",{style:"currency",currency:"EUR"}).format(value);
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const formatDate=value=>{const [y,m,d]=String(value).split("-").map(Number);return new Intl.DateTimeFormat("lv-LV").format(new Date(y,m-1,d));};

function priceHistoryMarkup(product){
  const history=state.history[product.canonicalProductId||product.productId||""];
  const points=(history?.points||[]).filter(point=>Number.isFinite(point.price)).slice(-60);
  const priceLabel="Zemākā novērotā cena";
  const pointPriceLabel=point=>point.requiresPaldies?"cena ar PALDIES":"akcijas cena";
  if(!points.length)return '<p class="history-note">Cenu vēsture vēl nav savākta.</p>';
  const first=points[0],last=points[points.length-1];
  if(points.length===1){
    const usual=Number.isFinite(last.regularPrice)?' · Parastā cena: '+money(last.regularPrice):"";
    const count=last.offerCount>1?' · Salīdzināti '+last.offerCount+' piedāvājuma varianti':"";
    return '<div class="history-summary"><strong>'+money(last.price)+'</strong><span>Pirmais novērojums: '+escapeHtml(formatDate(last.date))+usual+count+'</span></div><p class="history-note">Pašlaik ir tikai viens dienas mērījums. Pēc atkārtotiem atjauninājumiem būs redzama zemākās novērotās cenas dinamika.</p>';
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
  const promoCircles=points.map((point,index)=>'<circle class="chart-point promo-point" cx="'+x(index)+'" cy="'+y(point.price)+'" r="'+(index===points.length-1?5:3.5)+'"><title>'+escapeHtml(formatDate(point.date)+": "+pointPriceLabel(point)+" "+money(point.price))+'</title></circle>').join("");
  const regularCircles=points.map((point,index)=>Number.isFinite(point.regularPrice)?'<circle class="chart-point regular-point" cx="'+x(index)+'" cy="'+y(point.regularPrice)+'" r="3"><title>'+escapeHtml(formatDate(point.date)+": parastā cena "+money(point.regularPrice))+'</title></circle>':"").join("");
  const difference=last.price-first.price;
  const percent=first.price?difference/first.price*100:0;
  const trend=difference>.001?"price-up":difference<-.001?"price-down":"price-flat";
  const trendText=difference>.001?"Zemākā novērotā cena pieaugusi":difference<-.001?"Zemākā novērotā cena samazinājusies":"Zemākā novērotā cena nav mainījusies";
  const regularLine=regular.filter(Number.isFinite).length>1?'<polyline class="chart-line regular-line" points="'+regularCoordinates+'"/>':"";
  const legend='<div class="chart-legend"><span><i class="legend-swatch promo-swatch"></i>'+priceLabel+'</span><span><i class="legend-swatch regular-swatch"></i>Parastā cena</span></div>';
  return '<div class="history-summary"><strong>'+money(last.price)+'</strong><span>'+escapeHtml(formatDate(first.date))+' → '+escapeHtml(formatDate(last.date))+'</span><b class="'+trend+'">'+(difference>0?"+":"")+money(difference)+' ('+(percent>0?"+":"")+percent.toFixed(1)+'%)</b></div>'+legend+'<div class="chart-wrap"><svg class="price-chart" viewBox="0 0 '+width+' '+height+'" role="img" aria-label="Zemākās novērotās un parastās cenas izmaiņas laikā">'+grid+regularLine+'<polyline class="chart-line" points="'+promoCoordinates+'"/>'+regularCircles+promoCircles+'<text class="chart-date" x="'+left+'" y="'+(height-12)+'">'+escapeHtml(formatDate(first.date))+'</text><text class="chart-date" x="'+(width-right)+'" y="'+(height-12)+'" text-anchor="end">'+escapeHtml(formatDate(last.date))+'</text></svg></div><p class="history-note">'+trendText+'. Katras dienas punktā izmantota zemākā verificētā cena no atrastajiem Maxima piedāvājumiem. Atsevišķi piedāvājumu varianti saglabāti vēsturē.</p>';
}

function offerVariantMarkup(offer){
  const formats=Array.isArray(offer.storeFormats)&&offer.storeFormats.length
    ?"MAXIMA "+offer.storeFormats.join(", ")
    :"Veikala formāts nav apstiprināts";
  const validity=offer.validFrom&&offer.validUntil
    ?formatDate(offer.validFrom)+"–"+formatDate(offer.validUntil)
    :"Derīguma termiņš nav norādīts";
  const loyalty=offer.requiresPaldies
    ?'<span class="variant-loyalty">PALDIES</span>'
    :'<span class="variant-loyalty neutral">Bez kartes nosacījuma</span>';
  const regular=Number.isFinite(offer.regularPrice)&&offer.regularPrice>offer.price
    ?'<del>'+money(offer.regularPrice)+'</del>'
    :"";
  const discount=Number.isFinite(offer.discountPercent)
    ?'<span class="variant-discount">-'+escapeHtml(offer.discountPercent)+'%</span>'
    :"";
  return '<div class="offer-variant"><div class="variant-price"><strong>'+money(offer.price)+'</strong>'+regular+discount+'</div><div class="variant-meta"><strong>'+escapeHtml(offer.campaign||"Maxima piedāvājums")+'</strong><span>'+escapeHtml(formats)+'</span><span>'+escapeHtml(validity)+'</span></div><div class="variant-actions">'+loyalty+'<a href="'+escapeHtml(offer.source||offer.campaignSource||"#")+'" target="_blank" rel="noopener">Avots ↗</a></div></div>';
}

function render(){
  const query=document.querySelector("#search").value.trim().toLocaleLowerCase("lv");
  const city=document.querySelector("#city").value;
  const sort=document.querySelector("#sort").value;
  const items=state.products.filter(product=>{
    const variants=product.offers||[];
    const cityMatch=!city||variants.some(offer=>offer.city===city);
    const searchable=[product.name,product.brand,...(product.aliases||[]),...variants.map(offer=>offer.campaign),...variants.flatMap(offer=>offer.storeFormats||[])];
    return cityMatch&&searchable.some(value=>String(value??"").toLocaleLowerCase("lv").includes(query));
  });
  items.sort((a,b)=>sort==="price"?(a.lowestPrice??Infinity)-(b.lowestPrice??Infinity):sort==="name"?a.name.localeCompare(b.name,"lv"):(b.highestDiscountPercent??0)-(a.highestDiscountPercent??0));
  const offerCount=items.reduce((sum,product)=>sum+(product.offerCount||product.offers?.length||0),0);
  document.querySelector("#count").textContent=items.length+" produkti · "+offerCount+" piedāvājumi";
  document.querySelector("#offers").innerHTML=items.map(product=>{
    const variants=[...(product.offers||[])].sort((a,b)=>(a.price??Infinity)-(b.price??Infinity));
    const historyId=product.canonicalProductId||product.productId||"";
    const badge=Number.isFinite(product.highestDiscountPercent)
      ?'<span class="badge">Līdz -'+escapeHtml(product.highestDiscountPercent)+'%</span>'
      :"";
    const aliases=(product.aliases||[]).length>1
      ?'<div class="meta">Apvienoti '+product.aliases.length+' nosaukuma varianti</div>'
      :"";
    return '<article class="card"><div class="card-image">'+(product.image?'<img src="'+escapeHtml(product.image)+'" alt="" style="max-width:100%;max-height:100%;object-fit:contain">':'%')+'</div><div class="card-body">'+badge+'<h3>'+escapeHtml(product.name)+'</h3><div class="meta">'+escapeHtml([product.brand,product.packageSize].filter(Boolean).join(" · "))+'</div>'+aliases+'<div class="price-row"><span class="price">'+(Number.isFinite(product.lowestPrice)?money(product.lowestPrice):"Cena nav norādīta")+'</span><span class="meta">zemākā atrastā cena</span></div><div class="variant-heading">'+variants.length+' piedāvājuma varianti</div><div class="offer-variants">'+variants.map(offerVariantMarkup).join("")+'</div><button class="history-toggle" type="button" data-history-toggle="'+escapeHtml(historyId)+'" aria-expanded="false">Cenu vēsture <span aria-hidden="true">⌄</span></button><div class="history-panel" id="history-'+escapeHtml(historyId)+'" hidden>'+priceHistoryMarkup(product)+'</div></div></article>';
  }).join("");
  document.querySelector("#empty").hidden=items.length!==0;
}

async function init(){
  try{
    const response=await fetch("data/offers.json",{cache:"no-store"});
    if(!response.ok)throw new Error("Cannot load offers");
    const data=await response.json();

    if(Array.isArray(data.products)&&data.products.length){
      state.products=data.products;
    }else{
      state.products=(Array.isArray(data.offers)?data.offers:[]).map(offer=>({
        productId:offer.productId,
        canonicalProductId:offer.productId,
        name:offer.name,
        image:offer.image,
        lowestPrice:offer.price,
        highestDiscountPercent:offer.discountPercent,
        offerCount:1,
        offers:[offer],
      }));
    }

    try{
      const historyResponse=await fetch("data/price-history.json",{cache:"no-store"});
      if(historyResponse.ok){
        const historyData=await historyResponse.json();
        state.history=historyData.products||{};
      }
    }catch(error){console.warn("Price history unavailable",error);}

    const cities=[...new Set(state.products.flatMap(product=>(product.offers||[]).map(offer=>offer.city)).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"lv"));
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
