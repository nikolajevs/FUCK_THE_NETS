const state={offers:[]};
const money=value=>new Intl.NumberFormat("lv-LV",{style:"currency",currency:"EUR"}).format(value);
const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
function render(){
  const query=document.querySelector("#search").value.trim().toLocaleLowerCase("lv");
  const city=document.querySelector("#city").value;
  const sort=document.querySelector("#sort").value;
  const items=state.offers.filter(o=>(!city||o.city===city)&&[o.name,o.brand,o.store].some(v=>String(v??"").toLocaleLowerCase("lv").includes(query)));
  items.sort((a,b)=>sort==="price"?(a.price??Infinity)-(b.price??Infinity):sort==="name"?a.name.localeCompare(b.name,"lv"):(b.discountPercent??0)-(a.discountPercent??0));
  document.querySelector("#count").textContent=items.length+" piedāvājumi";
  document.querySelector("#offers").innerHTML=items.map(o=>`<article class="card">
    <div class="card-image">${o.image?'<img src="'+escapeHtml(o.image)+'" alt="" style="max-width:100%;max-height:100%;object-fit:contain">':'%'}</div>
    <div class="card-body">
      ${Number.isFinite(o.discountPercent)?'<span class="badge">-'+escapeHtml(o.discountPercent)+'%</span>':''}
      <h3>${escapeHtml(o.name)}</h3><div class="meta">${escapeHtml([o.brand,o.unit].filter(Boolean).join(" · "))}</div>
      <div class="price-row"><span class="price">${Number.isFinite(o.price)?money(o.price):"Cena nav norādīta"}</span>${Number.isFinite(o.regularPrice)?'<span class="old-price">'+money(o.regularPrice)+'</span>':''}</div>
      <div class="card-foot"><span>${escapeHtml([o.city,o.store].filter(Boolean).join(" · ")||"Vieta nav norādīta")}</span><span>${o.paldis?"PALDIES":""}</span></div>
      <div class="card-foot"><span>Derīgs līdz: ${escapeHtml(o.validUntil||"nav norādīts")}</span><a href="${escapeHtml(o.source||"#")}" target="_blank" rel="noopener">Avots ↗</a></div>
    </div></article>`).join("");
  document.querySelector("#empty").hidden=items.length!==0;
}
async function init(){
  try{
    const response=await fetch("data/offers.json",{cache:"no-store"});
    if(!response.ok)throw new Error("Cannot load offers");
    const data=await response.json();state.offers=Array.isArray(data.offers)?data.offers:[];
    const cities=[...new Set(state.offers.map(o=>o.city).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"lv"));
    document.querySelector("#city").innerHTML='<option value="">Visas pilsētas</option>'+cities.map(c=>'<option>'+escapeHtml(c)+'</option>').join("");
    document.querySelector("#updated").textContent=data.updatedAt?"Atjaunots: "+data.updatedAt:"";
  }catch(error){console.error(error);document.querySelector("#empty").hidden=false;document.querySelector("#empty h3").textContent="Neizdevās ielādēt piedāvājumus";}
  render();
}
["search","city","sort"].forEach(id=>document.querySelector("#"+id).addEventListener(id==="search"?"input":"change",render));
init();