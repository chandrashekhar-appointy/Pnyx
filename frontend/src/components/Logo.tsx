import React from "react";
import Image from "next/image";
import Link from "next/link";

interface LogoProps {
  isCollapsed: boolean;
}

const Logo = React.forwardRef<HTMLButtonElement, LogoProps>(({ isCollapsed }, ref) => {
  return (
    <Link href="/" className="cursor-pointer hover:opacity-80 transition-opacity flex items-center justify-center w-full">
      {isCollapsed ? (
        <img 
          src="/image.png" 
          alt="Pnyx Logo" 
          className="w-12 h-12 object-contain" 
        />
      ) : (
        <div className="flex items-center justify-center w-full py-2">
          <img 
            src="/image.png" 
            alt="Pnyx Logo" 
            className="w-40 h-40 object-contain" 
          />
        </div>
      )}
    </Link>
  );
});

Logo.displayName = "Logo";

export default Logo;