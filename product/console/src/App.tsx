import React from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ShellProvider } from "./app/shellContext";
import Header from "./app/Header";
import Nav from "./app/Nav";
import Live from "./views/live/Live";
import DetailView from "./views/detail";
import Demo from "./views/demo/Demo";
import ExecView from "./views/exec";

export default function App(){
  return (
    <ShellProvider>
      <BrowserRouter>
        <Header />
        <Nav />
        <main>
          <Routes>
            <Route path="/" element={<Live />} />
            <Route path="/calls/:id" element={<DetailView />} />
            <Route path="/exec" element={<ExecView />} />
            <Route path="/demo" element={<Demo />} />
          </Routes>
        </main>
      </BrowserRouter>
    </ShellProvider>
  )
}

