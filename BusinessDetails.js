"use client";

import { businessLine, useApp } from "./Providers";

export default function BusinessDetails() {
  const { site } = useApp();
  return <p>{businessLine(site) || "Business details will be added before launch."}</p>;
}
