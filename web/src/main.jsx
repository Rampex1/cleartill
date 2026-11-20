import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import Banner from "./Banner.jsx";
import Checkout from "./Checkout.jsx";
import Home from "./Home.jsx";
import ThreeDS from "./ThreeDS.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <Banner />
      <Routes>
        <Route path="/checkout/3ds/:pi" element={<ThreeDS />} />
        <Route path="/checkout/:cs" element={<Checkout />} />
        <Route path="*" element={<Home />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
