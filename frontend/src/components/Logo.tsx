import React from "react";
import Image from "next/image";
import Link from "next/link";

interface LogoProps {
  isCollapsed: boolean;
}

const Logo = React.forwardRef<HTMLButtonElement, LogoProps>(({ isCollapsed }, ref) => {
  return (
    <Link href="/" className="cursor-pointer hover:opacity-80 transition-opacity flex items-center justify-center">
      {isCollapsed ? (
        <img src="/image.svg" alt="Application Logo" style={{ width: 60, height: 60 }} className="object-contain" />
      ) : (
        <div className="flex items-center justify-center w-full mb-2 px-2">
          <img src="/image.svg" alt="Application Logo" style={{ width: 220, height: 120 }} className="object-contain" />
        </div>
      )}
    </Link>
  );
});

Logo.displayName = "Logo";

export default Logo;