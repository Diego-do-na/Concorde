import React from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { ShellProvider } from "./app/shellContext";
import Header from "./app/Header";
import Nav from "./app/Nav";
import Live from "./views/live/Live";
import DetailView from "./views/detail";

const Placeholder: React.FC<{text:string}> = ({text}) => (
  <div style={{padding:20}}>{text}</div>
)

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
            <Route path="/exec" element={<Placeholder text="Exec view lands with task Txxx" />} />
            <Route path="/demo" element={<Placeholder text="Demo view lands with task Txxx" />} />
          </Routes>
        </main>
      </BrowserRouter>
    </ShellProvider>
  )
}

