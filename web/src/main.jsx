import "@fontsource-variable/inter";
import "@fontsource-variable/schibsted-grotesk";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import Banner from "./Banner.jsx";
import Checkout from "./Checkout.jsx";
import { Login, Signup } from "./dashboard/AuthForm.jsx";
import Balance from "./dashboard/Balance.jsx";
import Customers from "./dashboard/Customers.jsx";
import Keys from "./dashboard/Keys.jsx";
import Layout from "./dashboard/Layout.jsx";
import Payment from "./dashboard/Payment.jsx";
import Payments from "./dashboard/Payments.jsx";
import WebhookEndpoint from "./dashboard/WebhookEndpoint.jsx";
import Webhooks from "./dashboard/Webhooks.jsx";
import DemoThanks from "./DemoThanks.jsx";
import Home from "./Home.jsx";
import ThreeDS from "./ThreeDS.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <Banner />
      <Routes>
        <Route path="/checkout/3ds/:pi" element={<ThreeDS />} />
        <Route path="/checkout/:cs" element={<Checkout />} />
        <Route path="/demo/thanks" element={<DemoThanks />} />
        <Route path="/dashboard/login" element={<Login />} />
        <Route path="/dashboard/signup" element={<Signup />} />
        <Route path="/dashboard" element={<Layout />}>
          <Route index element={<Navigate to="/dashboard/payments" replace />} />
          <Route path="payments" element={<Payments />} />
          <Route path="payments/:id" element={<Payment />} />
          <Route path="customers" element={<Customers />} />
          <Route path="balance" element={<Balance />} />
          <Route path="webhooks" element={<Webhooks />} />
          <Route path="webhooks/:id" element={<WebhookEndpoint />} />
          <Route path="keys" element={<Keys />} />
        </Route>
        <Route path="*" element={<Home />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
