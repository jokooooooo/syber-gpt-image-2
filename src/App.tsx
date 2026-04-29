import { Routes, Route } from 'react-router-dom';
import TopNavBar from './components/TopNavBar';
import SideNavBar from './components/SideNavBar';
import Home from './pages/Home';
import History from './pages/History';
import Favorites from './pages/Favorites';
import Config from './pages/Config';
import Account from './pages/Account';
import Billing from './pages/Billing';
import Login from './pages/Login';
import Register from './pages/Register';
import AnnouncementModal from './components/AnnouncementModal';
import TaskDrawer from './components/TaskDrawer';
import TaskToastStack from './components/TaskToastStack';

export default function App() {
  return (
    <div className="min-h-screen bg-background text-on-background font-mono overflow-x-hidden selection:bg-secondary-container selection:text-secondary">
      <TopNavBar />
      <AnnouncementModal />
      <TaskDrawer />
      <TaskToastStack />
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/history" element={
          <>
            <SideNavBar />
            <History />
          </>
        } />
        <Route path="/favorites" element={
          <>
            <SideNavBar />
            <Favorites />
          </>
        } />
        <Route path="/config" element={
          <>
            <SideNavBar />
            <Config />
          </>
        } />
        <Route path="/account" element={
          <>
            <SideNavBar />
            <Account />
          </>
        } />
        <Route path="/billing" element={
          <>
            <SideNavBar />
            <Billing />
          </>
        } />
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
      </Routes>
    </div>
  );
}
