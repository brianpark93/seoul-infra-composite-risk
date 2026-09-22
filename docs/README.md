# 인터랙티브 위험도 지도 — GitHub Pages 배포

정적 사이트다. 서버가 필요 없고, 빌드 도구도 필요 없다.
`index.html` · `style.css` · `app.js` · `data/*.json` 이 전부다.

## 무엇이 되는가

- 서울 테스트베드 5곳 × 시나리오 6개
- **시간 슬라이더 + 재생** — 위험도가 지도 위에서 변하는 과정
- **상호의존 전이 ON/OFF** — 끄면 직접위험도만
- **전달항 형식 전환** — diffusive ↔ saturating
- **K 슬라이더** — 링크별 위험전달계수를 움직이면 **브라우저가 즉시 재계산**
- 시설물 클릭 → E·V 설정근거, 유입 링크, 실시간 값
- 노드는 단일 원이며 현재 표시값으로 칠해진다 (직접위험도는 토글로 비교)
- **딥링크** — `#G/360/1/diffusive` 형식. 발표자료에 특정 시점 링크를 걸 수 있다

## 해석엔진이 브라우저에서 돈다

`app.js` 의 `solve()` 는 Python `src/model.py` 와 **같은 식·같은 dt·같은 스텝수**를 쓴다.

```
Transfer_ji = G_ji(t) · K_ji · max(Rj − Ri, 0)      [diffusive]
              G_ji(t) · K_ji · Rj · (1 − Ri)        [saturating]
R_i(t+dt)   = clip(R_i + ΔR_direct_i + ΣTransfer·dt, 0, rmax)
```

직접위험도 `R_direct` 는 K 와 무관하므로 Python 에서 균일격자(1,400점)로 미리 뽑아
보내고 브라우저는 선형보간만 한다. PCHIP 을 JS 로 재구현해 scipy 와 어긋나는 것을
피하려는 조치다.

**검증** — 6개 시나리오 30개 시설물의 해석종료 복합위험도를 Python 결과와 대조한
최대 오차 **3.2 × 10⁻⁶**.

## 데이터 갱신

시나리오 JSON 이나 OSM 캐시를 바꾼 뒤:

```bash
python build_web.py     # docs/data/*.json 재생성 (현재 0.67 MB)
```

## 로컬에서 보기

`file://` 로 열면 `fetch()` 가 CORS 로 막힌다. 반드시 로컬 서버로:

```bash
cd docs && python -m http.server 8765
# http://127.0.0.1:8765
```

## GitHub Pages 배포

이 폴더가 배포 대상이다. **Settings → Pages → Source `Deploy from a branch`,
Branch `main`, Folder `/docs`**. 워크플로 파일이 필요 없다
(현재 `gh` 토큰에 `workflow` 스코프가 없어 Actions 방식은 쓸 수 없다).

내용을 고친 뒤에는:

```bash
python build_web.py          # 데이터가 바뀐 경우에만
git add -A && git commit -m "docs: 지도 갱신"
git push
```

push 후 1~2분이면 반영된다.

## 배포 전 확인할 것

1. **공개 여부** — 이 저장소를 public 으로 열면 시나리오 구조, 서울시 공식
   침수원인 해석, K 산정근거가 전부 공개된다. 국토부 과제 산출물이므로
   **주관기관(성균관대) 동의를 먼저 받을 것.** Private 저장소에서도 Pages 는
   되지만 GitHub Pro/Team 이상이 필요하다.
2. **`outputs/` 는 커밋 금지** — 190 MB. 루트 `.gitignore` 에 이미 넣어두었다.
   웹은 `outputs/` 를 전혀 참조하지 않는다.
3. **회의록·원본 pptx 는 이 저장소에 없다.** 실무폴더에만 두고 옮기지 말 것.
4. **OSM 저작자 표시** — 기저도는 © OpenStreetMap contributors, ODbL.
   페이지 하단과 지도에 이미 표기돼 있으니 지우지 말 것. 파생 지오메트리를
   재배포하는 형태이므로 ODbL 조건이 따라온다.

## 브라우저

Canvas 2D 와 `fetch` 만 쓴다. 외부 라이브러리·지도 타일·API 키가 없다.
Chrome / Edge / Safari / Firefox 최신 버전. 1040 px 미만에서는 지도와 패널이
세로로 쌓인다.
