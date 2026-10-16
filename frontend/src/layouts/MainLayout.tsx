import { Suspense, type ReactNode } from 'react';
import Navigation from '../components/common/Navigation';
import './MainLayout.css';
import CompleteProfilePrompt from '../components/common/CompleteProfilePrompt';
import SignDocumentsPrompt from '../components/common/SignDocumentsPrompt';

interface MainLayoutProps {
  children: ReactNode;
}

const MainLayout = ({ children }: MainLayoutProps) => {
  return (
    <div className="main-layout">
      {/* Both asked once per sign-in, on whichever screen they land on. The
          profile comes first and the signature prompt waits its turn, so only
          one overlay is ever on screen. */}
      <CompleteProfilePrompt />
      <SignDocumentsPrompt />
      <Navigation />
      <main className="main-content">
        {/* The boundary sits INSIDE the layout on purpose: route chunks are
            lazy now, and a boundary wrapped around the whole app would blank
            the sidebar and header on every navigation. The fallback is sized
            to fill the content box rather than collapse it, so a page arriving
            does not shift anything that is already on screen. */}
        <Suspense fallback={<div className="main-content-loading" aria-busy="true" aria-label="Loading" />}>
          {children}
        </Suspense>
      </main>
    </div>
  );
};

export default MainLayout;

