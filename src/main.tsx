import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App.tsx';
import { AuthProvider } from './auth.tsx';
import { HomeFeedProvider } from './homeFeed.tsx';
import { SiteProvider } from './site.tsx';
import { TaskCenterProvider } from './tasks.tsx';
import './index.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <SiteProvider>
          <HomeFeedProvider>
            <TaskCenterProvider>
              <App />
            </TaskCenterProvider>
          </HomeFeedProvider>
        </SiteProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
