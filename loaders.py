"""CSS loading animations for the editor overlay; one is picked at random.

Each entry is a CSS string that styles the single spinner element `.ga-spinner`
(rename any source `.loader` selector to `.ga-spinner`). Keep every `@keyframes`
name unique across entries (prefix with `ga-`) so injected styles never clash.

To add one: paste a new triple-quoted CSS string into LOADERS.
"""

LOADERS = [
    # --- rolling dots (css-loaders l9) ---
    """
.ga-spinner{
  --r1:154%;
  --r2:68.5%;
  width:90px;
  aspect-ratio:1;
  border-radius:50%;
  background:
    radial-gradient(var(--r1) var(--r2) at top   ,#0000 79.5%,#269af2 80%),
    radial-gradient(var(--r1) var(--r2) at bottom,#269af2 79.5%,#0000 80%),
    radial-gradient(var(--r1) var(--r2) at top   ,#0000 79.5%,#269af2 80%),
    #ccc;
  background-size:50.5% 220%;
  background-position:-100% 0%,0% 0%,100% 0%;
  background-repeat:no-repeat;
  animation:ga-l9 2s infinite linear;}
@keyframes ga-l9{
  33% {background-position:   0% 33% ,100% 33% ,200% 33% }
  66% {background-position:-100% 66% ,0%   66% ,100% 66% }
  100%{background-position:   0% 100%,100% 100%,200% 100%}}
""",

    # --- writing pen (css-loaders l16) ---
    """
.ga-spinner{
  width:20px;
  height:80px;
  background:#935936;
  position:relative;}
.ga-spinner:before{
  content:"";
  position:absolute;
  top:10px;
  left:-22px;
  width:25px;
  height:60px;
  background:
    radial-gradient(farthest-side,#fff 92%,#0000) 60% 6px/4px 4px,
    radial-gradient(50% 60%,#53707b 92%,#0000) center/70% 55%,
    radial-gradient(farthest-side,#53707b 92%,#0000) 50% 3px/14px 14px,
    radial-gradient(142% 100% at bottom right,#0000 64%,#53707b 65%) bottom/57% 40%,
    conic-gradient(from -120deg at right,#53707b 36deg,#0000 0)100% 3px/51% 12px,
    conic-gradient(from 120deg at top left,#0000 ,#ef524a 2deg 40deg,#0000 43deg) top/100% 10px;
  background-repeat:no-repeat;
  transform:rotate(-26deg);
  transform-origin:100% 80%;
  animation:ga-l16 .25s infinite linear alternate;}
.ga-spinner:after{
  content:"";
  position:absolute;
  width:6px;
  height:12px;
  left:-6px;
  bottom:15px;
  border-radius:100px 0 0 100px;
  background:#53707b;}
@keyframes ga-l16{
  100%{transform:rotate(0)}}
""",

    # --- clouds drifting (css-loaders l10) ---
    """
.ga-spinner{
  width:80px;
  height:40px;
  background:
    radial-gradient(circle 25px at top right, #ffd738 40%,#0000 ),
    #4dbefa;
  position:relative;
  overflow:hidden;}
.ga-spinner:before,
.ga-spinner:after{
  content:"";
  position:absolute;
  top:4px;
  left:-40px;
  width:36px;
  height:20px;
  --c:radial-gradient(farthest-side,#fff 96%,#0000);
  background:
    var(--c) 100% 100% /30% 60%,
    var(--c) 70% 0 /50% 100%,
    var(--c) 0 100% /36% 68%,
    var(--c) 27% 18% /26% 40%,
    linear-gradient(#fff 0 0) bottom/67% 58%;
  background-repeat:no-repeat;
  animation:ga-l10 2s linear infinite;}
.ga-spinner:after{
  top:15px;
  --l:200%;}
@keyframes ga-l10{
  100%{left:var(--l,105%)}}
""",

    # --- "Loading" + rocket liftoff (css-loaders l10 text variant) ---
    """
.ga-spinner{
  width:fit-content;
  font-size:17px;
  font-family:monospace;
  line-height:1.4;
  font-weight:bold;
  padding:30px 2px 50px;
  background:linear-gradient(#000 0 0) 0 0/100% 100% content-box padding-box no-repeat;
  position:relative;
  overflow:hidden;
  animation:ga-l10t-0 2s infinite cubic-bezier(1,175,.5,175);}
.ga-spinner::before{
  content:"Loading";
  display:inline-block;
  animation:ga-l10t-2 2s infinite;}
.ga-spinner::after{
  content:"";
  position:absolute;
  width:34px;
  height:28px;
  top:110%;
  left:calc(50% - 16px);
  background:
    linear-gradient(90deg,#0000 12px,#f92033 0 22px,#0000 0 26px,#fdc98d 0 32px,#0000) bottom 26px left 50%,
    linear-gradient(90deg,#0000 10px,#f92033 0 28px,#fdc98d 0 32px,#0000 0) bottom 24px  left 50%,
    linear-gradient(90deg,#0000 10px,#643700 0 16px,#fdc98d 0 20px,#000 0 22px,#fdc98d 0 24px,#000 0 26px,#f92033 0 32px,#0000 0) bottom 22px left 50%,
    linear-gradient(90deg,#0000 8px,#643700 0 10px,#fdc98d 0 12px,#643700 0 14px,#fdc98d 0 20px,#000 0 22px,#fdc98d 0 28px,#f92033 0 32px,#0000 0) bottom 20px left 50%,
    linear-gradient(90deg,#0000 8px,#643700 0 10px,#fdc98d 0 12px,#643700 0 16px,#fdc98d 0 22px,#000 0 24px,#fdc98d 0 30px,#f92033 0 32px,#0000 0) bottom 18px left 50%,
    linear-gradient(90deg,#0000 8px,#643700 0 12px,#fdc98d 0 20px,#000 0 28px,#f92033 0 30px,#0000 0) bottom 16px left 50%,
    linear-gradient(90deg,#0000 12px,#fdc98d 0 26px,#f92033 0 30px,#0000 0) bottom 14px left 50%,
    linear-gradient(90deg,#fdc98d 6px,#f92033 0 14px,#222a87 0 16px,#f92033 0 22px,#222a87 0 24px,#f92033 0 28px,#0000 0 32px,#643700 0) bottom 12px left 50%,
    linear-gradient(90deg,#fdc98d 6px,#f92033 0 16px,#222a87 0 18px,#f92033 0 24px,#f92033 0 26px,#0000 0 30px,#643700 0) bottom 10px left 50%,
    linear-gradient(90deg,#0000 10px,#f92033 0 16px,#222a87 0 24px,#feee49 0 26px,#222a87 0 30px, #643700 0) bottom 8px left 50%,
    linear-gradient(90deg,#0000 12px,#222a87 0 18px,#feee49 0 20px,#222a87 0 30px,#643700 0) bottom 6px left 50%,
    linear-gradient(90deg,#0000 8px,#643700 0 12px,#222a87 0 30px,#643700 0) bottom 4px left 50%,
    linear-gradient(90deg,#0000 6px,#643700 0 14px,#222a87 0 26px,#0000 0) bottom 2px left 50%,
    linear-gradient(90deg,#0000 6px,#643700 0 10px,#0000 0 ) bottom 0px left 50%;
  background-size:34px 2px;
  background-repeat:no-repeat;
  animation:inherit;
  animation-name:ga-l10t-1;}
@keyframes ga-l10t-0{
  0%,30%   { background-position: 0 0px }
  50%,100% { background-position: 0 -0.1px }}
@keyframes ga-l10t-1{
  50%,100% { top:109.5% }}
@keyframes ga-l10t-2{
  0%,30%   { transform:translateY(0); }
  80%,100% { transform:translateY(-260%); }}
""",

    # --- flipping arrow halves (css-loaders l13) ---
    """
.ga-spinner{
  width:60px;
  aspect-ratio:1;
  display:flex;
  animation:ga-l13-0 4s infinite linear .5s;}
.ga-spinner::before,
.ga-spinner::after{
  content:"";
  flex:1;
  background:#FA6900;
  clip-path:polygon(50% 0,100% 0,100% 100%,50% 100%,0 75%,0 25%);
  animation:ga-l13-1 1s infinite linear;}
.ga-spinner::after{
  transform:scale(-1);
  animation:none;}
@keyframes ga-l13-0 {
  0%   ,12.49% {transform: rotate(0deg)}
  12.5%,37.49% {transform: rotate(90deg)}
  37.5%,62.49% {transform: rotate(180deg)}
  62.5%,87.49% {transform: rotate(270deg)}
  87.5%,100%   {transform: rotate(360deg)}}
@keyframes ga-l13-1 {
  0%,
  5%   {transform:translate(0px)   perspective(150px) rotateY(0deg) }
  33%  {transform:translate(-10px) perspective(150px) rotateX(0deg) }
  66%  {transform:translate(-10px) perspective(150px) rotateX(-180deg)}
  95%,
  100%{transform: translate(0px)   perspective(150px) rotateX(-180deg)}}
""",

    # --- nested gears (css-loaders l3) ---
    """
.ga-spinner{
  display:inline-grid;
  width:80px;
  aspect-ratio:1;}
.ga-spinner:before,
.ga-spinner:after{
  content:"";
  grid-area:1/1;
  border-radius:50%;
  animation:ga-l3-0 2s alternate infinite ease-in-out;}
.ga-spinner:before{
  margin:25%;
  background:repeating-conic-gradient(#C02942 0 60deg,#0B486B 0 120deg);
  translate:0 50%;
  rotate:-150deg;}
.ga-spinner:after{
  padding:10%;
  margin:-10%;
  background:repeating-conic-gradient(#0B486B 0 30deg,#C02942 0 60deg);
  mask:linear-gradient(#0000 50%,#000 0) content-box exclude,linear-gradient(#0000 50%,#000 0);
  rotate:-75deg;
  animation-name:ga-l3-1;}
@keyframes ga-l3-0 {to{rotate: 150deg}}
@keyframes ga-l3-1 {to{rotate:  75deg}}
""",

    # --- orbiting dots between poles (css-loaders l49) ---
    """
.ga-spinner{
  height:15px;
  aspect-ratio:4;
  --_g:no-repeat radial-gradient(farthest-side,#000 90%,#0000);
  background:
    var(--_g) left,
    var(--_g) right;
  background-size:25% 100%;
  display:grid;}
.ga-spinner:before,
.ga-spinner:after{
  content:"";
  height:inherit;
  aspect-ratio:1;
  grid-area:1/1;
  margin:auto;
  border-radius:50%;
  transform-origin:-100% 50%;
  background:#000;
  animation:ga-l49 1s infinite linear;}
.ga-spinner:after{
  transform-origin:200% 50%;
  --s:-1;
  animation-delay:-.5s;}
@keyframes ga-l49 {
  58%,
  100% {transform: rotate(calc(var(--s,1)*1turn))}}
""",

    # --- orbiting planets (css-loaders l17) ---
    """
.ga-spinner{
  width:70px;
  aspect-ratio:1;
  background:
    radial-gradient(farthest-side,#ffa516 90%,#0000) center/16px 16px,
    radial-gradient(farthest-side,green   90%,#0000) bottom/12px 12px;
  background-repeat:no-repeat;
  animation:ga-l17 1s infinite linear;
  position:relative;}
.ga-spinner::before{
  content:"";
  position:absolute;
  width:8px;
  aspect-ratio:1;
  inset:auto 0 16px;
  margin:auto;
  background:#ccc;
  border-radius:50%;
  transform-origin:50% calc(100% + 10px);
  animation:inherit;
  animation-duration:0.5s;}
@keyframes ga-l17 {
  100%{transform: rotate(1turn)}}
""",

    # --- steaming cup (css-loaders) ---
    """
.ga-spinner {
  width: 24px;
  height: 80px;
  display: block;
  margin: 35px auto 0;
  border: 1px solid #FFF;
  border-radius: 0 0 50px 50px;
  position: relative;
  box-shadow: 0px 0px #FF3D00 inset;
  background-image: linear-gradient(#FF3D00 100px, transparent 0);
  background-position: 0px 0px;
  background-size: 22px 80px;
  background-repeat: no-repeat;
  box-sizing: border-box;
  animation: ga-cup 6s linear infinite;
}
.ga-spinner::after {
  content: '';
  box-sizing: border-box;
  top: -6px;
  left: 50%;
  transform: translateX(-50%);
  position: absolute;
  border: 1px solid #FFF;
  border-radius: 50%;
  width: 28px;
  height: 6px;
}
.ga-spinner::before {
  content: '';
  box-sizing: border-box;
  left: 0;
  bottom: -4px;
  border-radius: 50%;
  position: absolute;
  width: 6px;
  height: 6px;
  animation: ga-cup1 6s linear infinite;
}
@keyframes ga-cup {
  0% {
    background-position: 0px 80px;
  }
  100% {
    background-position: 0px 0px;
  }
}
@keyframes ga-cup1 {
  0% {
    box-shadow: 4px -10px rgba(255, 255, 255, 0), 6px 0px rgba(255, 255, 255, 0), 8px -15px rgba(255, 255, 255, 0), 12px 0px rgba(255, 255, 255, 0);
  }
  20% {
    box-shadow: 4px -20px rgba(255, 255, 255, 0), 8px -10px rgba(255, 255, 255, 0), 10px -30px rgba(255, 255, 255, 0.5), 15px -5px rgba(255, 255, 255, 0);
  }
  40% {
    box-shadow: 2px -40px rgba(255, 255, 255, 0.5), 8px -30px rgba(255, 255, 255, 0.4), 8px -60px rgba(255, 255, 255, 0.5), 12px -15px rgba(255, 255, 255, 0.5);
  }
  60% {
    box-shadow: 4px -60px rgba(255, 255, 255, 0.5), 6px -50px rgba(255, 255, 255, 0.4), 10px -90px rgba(255, 255, 255, 0.5), 15px -25px rgba(255, 255, 255, 0.5);
  }
  80% {
    box-shadow: 2px -80px rgba(255, 255, 255, 0.5), 4px -70px rgba(255, 255, 255, 0.4), 8px -120px rgba(255, 255, 255, 0), 12px -35px rgba(255, 255, 255, 0.5);
  }
  100% {
    box-shadow: 4px -100px rgba(255, 255, 255, 0), 8px -90px rgba(255, 255, 255, 0), 10px -120px rgba(255, 255, 255, 0), 15px -45px rgba(255, 255, 255, 0);
  }
}
""",

    # --- balancing seesaw (css-loaders) ---
    """
.ga-spinner{
  display: block;
  position: relative;
  height: 32px;
  width: 120px;
  border-bottom: 5px solid #fff;
  box-sizing: border-box;
  animation: ga-balancing 2s linear infinite alternate;
  transform-origin: 50% 100%;
}
.ga-spinner:before{
  content: '';
  position: absolute;
  left: 0;
  bottom: 0;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #FF3D00;
  animation: ga-ballbns 2s linear infinite alternate;
}
.ga-spinner:after{
  content: '';
  position: absolute;
  left: 50%;
  bottom: 0;
  height: 20px;
  width: 20px;
  transform: translate(-50%, 100%);
  border-radius: 50%;
  border: 6px solid #fff;
}
@keyframes ga-ballbns {
  0% {  left: 0; transform: translateX(0%); }
  100% {  left: 100%; transform: translateX(-100%); }
}
@keyframes ga-balancing {
  0% {  transform: rotate(-15deg); }
  50% {  transform: rotate(0deg); }
  100% {  transform: rotate(15deg); }
}
""",

    # --- magnifying glass loupe (css-loaders) ---
    """
.ga-spinner {
  position: relative;
  width: 120px;
  height: 140px;
  background-image: radial-gradient(circle 30px, #fff 100%, transparent 0),
  radial-gradient(circle 5px, #fff 100%, transparent 0),
  radial-gradient(circle 5px, #fff 100%, transparent 0),
  linear-gradient(#FFF 20px, transparent 0);
  background-position: center 127px , 94px 102px , 16px 18px, center 114px;
  background-size: 60px 60px, 10px 10px , 10px 10px , 4px 14px;
  background-repeat: no-repeat;
  z-index: 10;
  perspective: 500px;
}
.ga-spinner::before {
  content: '';
  position: absolute;
  width: 100px;
  height: 100px;
  border-radius:50%;
  border: 3px solid #fff;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -55%) rotate(-45deg);
  border-right-color: transparent;
  box-sizing: border-box;
}
.ga-spinner::after {
  content: '';
  position: absolute;
  height: 80px;
  width: 80px;
  transform: translate(-50%, -55%) rotate(-45deg) rotateY(0deg) ;
  left: 50%;
  top: 50%;
  box-sizing: border-box;
  border: 7px solid #FF3D00;
  border-radius:50%;
  animation: ga-loupe-rotate 0.5s linear infinite;
}
@keyframes ga-loupe-rotate {
  to{transform: translate(-50%, -55%) rotate(-45deg) rotateY(360deg)   }
}
""",

    # --- crossed spinning axes (css-loaders) ---
    """
.ga-spinner {
  position: relative;
  width: 164px;
  height: 164px;
  perspective: 300px;
}
.ga-spinner::before  {
  content: '';
  position: absolute;
  width: 100%;
  height: 100%;
  left: 0;
  top: 0;
  background-image:
    radial-gradient(circle 15px, #FF3D00 100%, transparent 0),
    radial-gradient(circle 15px, #FF3D00 100%, transparent 0),
    linear-gradient(#FF3D00 100px, transparent 0),
    linear-gradient(#FF3D00 100px, transparent 0);
  background-repeat: no-repeat;
  background-size: 30px 30px, 30px 30px, 40% 2px, 40% 2px;
  background-position: left center, right center, left center, right center;
  animation: ga-axis-y 1s linear infinite;
}
.ga-spinner::after  {
  content: '';
  position: absolute;
  width: 100%;
  height: 100%;
  left: 0;
  top: 0;
  background-image:
    radial-gradient(circle 15px, #fff 100%, transparent 0),
    radial-gradient(circle 15px, #fff 100%, transparent 0),
    linear-gradient(#fff 100px, transparent 0),
    linear-gradient(#fff 100px, transparent 0);
  background-repeat: no-repeat;
  background-size: 30px 30px, 30px 30px, 2px 40% , 2px 40%;
  background-position: top center, bottom center, top center, bottom center;
  animation: ga-axis-x 1s linear infinite;
}
@keyframes ga-axis-y {
  0%{ transform: rotateY(0deg)}
  100% { transform: rotateY(-180deg)}
}
@keyframes ga-axis-x {
  0%, 25% { transform: rotateX(0deg)}
  75%,  100% { transform: rotateX(-180deg)}
}
""",

    # --- frying pan + egg (css-loaders) ---
    """
.ga-spinner {
  width: 100px;
  height: 100px;
  display: block;
  margin: auto;
  position: relative;
  background: #222;
  border-radius: 50%;
  box-sizing: border-box;
  transform-origin: 170px 50px;
  border: 4px solid #333;
  box-shadow: 3px 4px #0003 inset, 0 0 6px #0002 inset;
  animation: ga-panmov 0.4s ease-in-out infinite alternate;
}
.ga-spinner::before {
  content: '';
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%) skew(-15deg, 15deg) rotate(-15deg);
  width: 55px;
  height: 53px;
  background: #fff;
  background-image:
  radial-gradient(circle 3px , #fff6 90%, transparent 10%),
  radial-gradient(circle 12px , #ffc400 90%, transparent 10%),
  radial-gradient(circle 12px , #ffae00 100%, transparent 0);
  background-repeat: no-repeat;
  background-position: -4px -6px , -2px -2px , -1px -1px;
  box-shadow: -2px -3px #0002 inset, 0 0 4px #0003 inset;
  border-radius: 47% 36% 50% 50% / 49% 45% 42% 44%;
  animation: ga-ylmov 0.6s ease-in-out infinite alternate;
}
.ga-spinner::after {
  content: '';
  position: absolute;
  left: 100%;
  top: 48px;
  height: 15px;
  width: 70px;
  background: #222222;
  border-radius: 0 8px 8px 0;
  box-shadow: 3px 0 3px #eee2 inset;
  transform: rotate(5deg) translateX(3px);
}
@keyframes ga-panmov {
  0% , 10% { transform: rotate(5deg) }
  90% , 100% { transform: rotate(-5deg) }
}
@keyframes ga-ylmov {
  to {
      border-radius: 50% 36% 50% 50% / 49% 50% 45% 45%;
      background-position: -2px -4px , 2px 2px , 1px 1px;
   }
}
""",

    # --- pan tossing egg (css-loaders) ---
    """
.ga-spinner {
  position: relative;
  width: 120px;
  height: 14px;
  border-radius: 0 0 15px 15px;
  background-color: #3e494d;
  box-shadow: 0 -1px 4px #5d6063 inset;
  animation: ga-panex 0.5s linear alternate infinite;
  transform-origin: 170px 0;
  z-index: 10;
  perspective: 300px;
}
.ga-spinner::before {
  content: '';
  position: absolute;
  left: calc( 100% - 2px);
  top: 0;
  z-index: -2;
  height: 10px;
  width: 70px;
  border-radius: 0 4px 4px 0;
  background-repeat: no-repeat;
  background-image: linear-gradient(#6c4924, #4b2d21), linear-gradient(#4d5457 24px, transparent 0), linear-gradient(#9f9e9e 24px, transparent 0);
  background-size: 50px 10px , 4px 8px , 24px 4px;
  background-position: right center , 17px center , 0px center;
}
.ga-spinner::after {
  content: '';
  position: absolute;
  left: 50%;
  top: 0;
  z-index: -2;
  transform: translate(-50% , -20px) rotate3d(75, -2, 3, 78deg);
  width: 55px;
  height: 53px;
  background: #fff;
  background-image:
  radial-gradient(circle 3px , #fff6 90%, transparent 10%),
  radial-gradient(circle 12px , #ffc400 90%, transparent 10%),
  radial-gradient(circle 12px , #ffae00 100%, transparent 0);
  background-repeat: no-repeat;
  background-position: -4px -6px , -2px -2px , -1px -1px;
  box-shadow: -2px -3px #0002 inset, 0 0 4px #0003 inset;
  border-radius: 47% 36% 50% 50% / 49% 45% 42% 44%;
  animation: ga-eggRst 1s ease-out infinite;
}
@keyframes ga-eggRst {
  0% ,  100%{  transform: translate(-50%, -20px) rotate3d(90, 0, 0, 90deg); opacity: 0; }
  10% , 90% {  transform: translate(-50%, -30px) rotate3d(90, 0, 0, 90deg); opacity: 1; }
  25%  {transform:  translate(-50% , -40px) rotate3d(85, 17, 2, 70deg) }
  75% {transform:  translate(-50% , -40px) rotate3d(75, -3, 2, 70deg) }
  50% {transform:  translate(-55% , -50px) rotate3d(75, -8, 3, 50deg) }
}
@keyframes ga-panex {
  0%{  transform: rotate(-5deg)  }
  100%{  transform: rotate(10deg)  }
}
""",

    # --- cassette tape (css-loaders) ---
    """
.ga-spinner {
  margin: auto;
  width: 100px;
  height: 30px;
  overflow: hidden;
  position: relative;
  background: rgba(0, 0, 0, 0.3);
  border-radius: 5px;
  box-shadow: 0px 35px 0 -5px #aaa, 0 -5px 0 0px #ddd, 0 -25px 0 -5px #fff,
    -25px -30px 0 0px #ddd, -25px 30px 0 0px #ddd, 25px -30px 0 0px #ddd,
    25px 30px 0 0px #ddd, 20px 10px 0 5px #ddd, 20px -10px 0 5px #ddd,
    -20px -10px 0 5px #ddd, -20px 10px 0 5px #ddd;
}
.ga-spinner:after,
.ga-spinner:before {
  content: "";
  border-radius: 100%;
  width: 35px;
  height: 35px;
  display: block;
  position: absolute;
  border: 4px dashed #fff;
  bottom: -4px;
  transform: rotate(0deg);
  box-sizing: border-box;
  animation: ga-tape 4s linear infinite;
}
.ga-spinner:before {
  right: 0;
  box-shadow: 0 0 0 4px #fff, 0 0 0 34px #000;
}
.ga-spinner:after {
  left: 0;
  box-shadow: 0 0 0 4px #fff, 0 0 0 65px #000;
}
@keyframes ga-tape {
  0% {
    transform: rotate(0deg) scale(0.4);
  }
  100% {
    transform: rotate(-360deg) scale(0.4);
  }
}
""",
]
